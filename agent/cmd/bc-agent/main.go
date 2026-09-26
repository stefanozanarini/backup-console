// bc-agent: agente della Backup Console per QNAP, server Veeam e PC con Veeam Agent.
//
//	bc-agent enroll -server https://agents.example.com -token XXXX [-allow-shell]
//	bc-agent run
//	bc-agent collect      (stampa i dati raccolti, senza inviarli)
//	bc-agent version
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"syscall"

	"backupconsole/agent/internal/agent"
	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/config"
	"backupconsole/agent/internal/identity"
	"backupconsole/agent/internal/version"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "enroll":
		err = enroll(args)
	case "run":
		err = run(args)
	case "collect":
		err = collect()
	case "version":
		fmt.Println(version.Version)
	default:
		usage()
	}
	if err != nil {
		log.Fatal(err)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "uso: bc-agent enroll|run|collect|version [opzioni]")
	os.Exit(2)
}

func enroll(args []string) error {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	cfgPath := fs.String("config", config.DefaultPath(), "file di configurazione")
	server := fs.String("server", "", "URL endpoint agenti, es. https://agents.example.com")
	token := fs.String("token", "", "token di enrollment generato dalla console")
	serverCA := fs.String("server-ca", "", "CA extra per il certificato TLS del server (sviluppo)")
	allowShell := fs.Bool("allow-shell", false, "consente shell e comandi custom dalla console (non modificabile da remoto)")
	_ = fs.Parse(args)
	if *server == "" || *token == "" {
		return fmt.Errorf("-server e -token sono obbligatori")
	}

	cfg := config.New(*cfgPath, *server)
	cfg.ServerCAFile = *serverCA
	cfg.AllowShell = *allowShell

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
		Token: *token, CSRPEM: csr, Hostname: host, Platform: runtime.GOOS, Arch: runtime.GOARCH,
		AgentVersion: version.Version, Roles: agent.Roles(collectors), AllowShell: cfg.AllowShell,
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
	fmt.Printf("enrollment completato: agent %s, stato %s\n", resp.AgentID, resp.Status)
	if resp.Status == "pending" {
		fmt.Println("approvare l'agente dalla console, poi avviare 'bc-agent run'")
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
	cert, err := identity.Load(cfg.DataDir)
	if err != nil {
		return err
	}
	c, err := client.New(cfg.ServerURL, &cert, cfg.ServerCAFile)
	if err != nil {
		return err
	}
	// TODO: su Windows girare come servizio (golang.org/x/sys/windows/svc).
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return agent.New(cfg, c).Run(ctx)
}

func collect() error {
	ctx := context.Background()
	cs := agent.Detect()
	out := map[string]any{
		"roles":     agent.Roles(cs),
		"inventory": agent.CollectInventory(ctx, cs),
		"metrics":   agent.CollectMetrics(ctx, cs),
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(out)
}
