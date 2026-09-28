// bc-agent: agente della Backup Console per QNAP, server Veeam e PC con Veeam Agent.
//
//	bc-agent enroll  -server https://agents.example.com -token XXXX [-disable-remote-exec]
//	bc-agent run
//	bc-agent install -server ... -token ... [-disable-remote-exec]   (Windows, da amministratore)
//	bc-agent uninstall [-purge]                                        (Windows)
//	bc-agent collect      (stampa i dati raccolti, senza inviarli)
//	bc-agent version
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"

	"backupconsole/agent/internal/agent"
	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/config"
	"backupconsole/agent/internal/identity"
	"backupconsole/agent/internal/upgrade"
	"backupconsole/agent/internal/version"
	"backupconsole/agent/internal/winsvc"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "enroll":
		err = enrollCmd(args)
	case "run":
		err = run(args)
	case "install":
		err = install(args)
	case "uninstall":
		err = uninstall(args)
	case "collect":
		err = collect()
	case "version":
		fmt.Println(version.Version)
	default:
		usage()
	}
	if errors.Is(err, agent.ErrRestart) {
		os.Exit(upgrade.ExitRestart)
	}
	if err != nil {
		log.Fatal(err)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "uso: bc-agent enroll|run|install|uninstall|collect|version [opzioni]")
	os.Exit(2)
}

type enrollOpts struct {
	cfgPath, server, token, serverCA string
	disableExec, allowUnsigned       bool
}

func enrollFlags(fs *flag.FlagSet) *enrollOpts {
	o := &enrollOpts{}
	fs.StringVar(&o.cfgPath, "config", config.DefaultPath(), "file di configurazione")
	fs.StringVar(&o.server, "server", "", "URL endpoint agenti, es. https://agents.example.com")
	fs.StringVar(&o.token, "token", "", "token di enrollment generato dalla console")
	fs.StringVar(&o.serverCA, "server-ca", "", "CA extra per il certificato TLS del server (sviluppo)")
	fs.BoolVar(&o.disableExec, "disable-remote-exec", false, "impedisce l'esecuzione di comandi dalla console (non modificabile da remoto)")
	fs.BoolVar(&o.allowUnsigned, "allow-unsigned-upgrades", false, "solo sviluppo: accetta aggiornamenti non firmati")
	return o
}

func enrollCmd(args []string) error {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	o := enrollFlags(fs)
	_ = fs.Parse(args)
	return enroll(o)
}

func enroll(o *enrollOpts) error {
	if o.server == "" || o.token == "" {
		return fmt.Errorf("-server e -token sono obbligatori")
	}
	cfg := config.New(o.cfgPath, o.server)
	cfg.ServerCAFile = o.serverCA
	cfg.DisableRemoteExec = o.disableExec
	cfg.AllowUnsignedUpgrades = o.allowUnsigned

	host, _ := os.Hostname()
	csr, err := identity.NewCSR(cfg.DataDir, host)
	if err != nil {
		return fmt.Errorf("generazione chiave: %w", err)
	}
	c, err := client.New(cfg.ServerURL, nil, cfg.ServerCAFile)
	if err != nil {
		return err
	}
	collectors := agent.Detect()
	var resp client.EnrollResponse
	err = c.Post(context.Background(), "/agent/v1/enroll", client.EnrollRequest{
		Token: o.token, CSRPEM: csr, Hostname: host, Platform: runtime.GOOS, Arch: runtime.GOARCH,
		AgentVersion: version.Version, Roles: agent.Roles(collectors), AllowShell: cfg.RemoteExecAllowed(),
	}, &resp)
	if err != nil {
		return fmt.Errorf("enrollment: %w", err)
	}
	if err := identity.Save(cfg.DataDir, resp.CertPEM, resp.CAPEM); err != nil {
		return err
	}
	cfg.AgentID = resp.AgentID
	if err := cfg.Save(); err != nil {
		return err
	}
	fmt.Printf("enrollment completato: agent %s, stato %s, esecuzione remota %s\n",
		resp.AgentID, resp.Status, map[bool]string{true: "attiva", false: "disattivata"}[cfg.RemoteExecAllowed()])
	if resp.Status == "pending" {
		fmt.Println("approvare il dispositivo dalla console")
	}
	return nil
}

func run(args []string) error {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	cfgPath := fs.String("config", config.DefaultPath(), "file di configurazione")
	_ = fs.Parse(args)

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		return err
	}
	if winsvc.IsService() {
		closeLog := logToFile(filepath.Join(cfg.DataDir, "agent.log"))
		defer closeLog()
		syncTrayFromService()
		// Uscita con errore → il Service Control Manager riavvia il servizio.
		return winsvc.Run(func(ctx context.Context) error { return runAgent(ctx, cfg) }, upgrade.ExitRestart)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return runAgent(ctx, cfg)
}

func runAgent(ctx context.Context, cfg *config.Config) error {
	cert, err := identity.Load(cfg.DataDir)
	if err != nil {
		return err
	}
	c, err := client.New(cfg.ServerURL, &cert, cfg.ServerCAFile)
	if err != nil {
		return err
	}
	return agent.New(cfg, c).Run(ctx)
}

// logToFile: sotto il servizio Windows non c'è una console; log in ProgramData con
// una rotazione minima all'avvio.
func logToFile(path string) func() {
	if st, err := os.Stat(path); err == nil && st.Size() > 5<<20 {
		_ = os.Rename(path, path+".1")
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return func() {}
	}
	log.SetOutput(f)
	return func() { _ = f.Close() }
}

func collect() error {
	ctx := context.Background()
	cs := agent.Detect()
	out := map[string]any{
		"roles":     agent.Roles(cs),
		"platform":  upgrade.Platform(),
		"inventory": agent.CollectInventory(ctx, cs),
		"metrics":   agent.CollectMetrics(ctx, cs),
	}
	reports := map[string]any{}
	for _, c := range cs {
		if r, ok := c.(agent.Reporter); ok {
			_, p, err := r.Report(ctx)
			if err != nil {
				reports[c.Name()] = map[string]string{"error": err.Error()}
			} else {
				reports[c.Name()] = p
			}
		}
	}
	if len(reports) > 0 {
		out["reports"] = reports
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(out)
}
