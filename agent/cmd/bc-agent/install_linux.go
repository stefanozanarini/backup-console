//go:build linux

package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Installazione su Linux con systemd (NAS Synology con DSM 7, server Linux). Sui QNAP
// si usa il QPKG, che ha il suo script di avvio.
const (
	linuxUnit    = "bc-agent.service"
	linuxUnitDir = "/etc/systemd/system"
)

// Unit systemd: riavvia l'agente se termina (anche dopo un aggiornamento, codice 75) e
// non dipende da alcuna sessione di terminale.
func unitFile(bin, cfg string) string {
	return fmt.Sprintf(`[Unit]
Description=Backup Console Agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=%s run -config %s
Restart=always
RestartSec=10
# L'agente scrive il suo log su stderr: lo raccoglie journald.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
`, bin, cfg)
}

// Script per l'attività pianificata "all'avvio" di DSM: se un aggiornamento di DSM ha
// tolto la unit systemd, la ricrea (mantenendo la registrazione) e avvia l'agente.
func ensureScript(bin, cfg string) string {
	return fmt.Sprintf(`#!/bin/sh
# Backup Console Agent: da eseguire all'avvio come root (Pianificazione attività di DSM).
if [ ! -f %[3]s/%[4]s ]; then
	%[1]s install -config %[2]s >/dev/null 2>&1
fi
systemctl start %[4]s
`, bin, cfg, linuxUnitDir, linuxUnit)
}

func systemctl(args ...string) error {
	out, err := exec.Command("systemctl", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("systemctl %s: %v: %s", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return nil
}

func install(args []string) error {
	fs := flag.NewFlagSet("install", flag.ExitOnError)
	dir := fs.String("dir", "/usr/local/bc-agent", "cartella di installazione (fuori da quelle che gli aggiornamenti di sistema riscrivono)")
	o := enrollFlags(fs)
	reenroll := fs.Bool("reenroll", false, "registra di nuovo anche se esiste già una registrazione (nuova identità)")
	_ = fs.Parse(args)

	if os.Geteuid() != 0 {
		return errors.New("serve root (su Synology: sudo -i)")
	}
	if _, err := exec.LookPath("systemctl"); err != nil {
		return errors.New("systemd non trovato: su questo sistema installare l'agente a mano")
	}
	// Configurazione e chiave accanto al binario, se non indicato diversamente.
	explicitCfg := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "config" {
			explicitCfg = true
		}
	})
	if !explicitCfg {
		o.cfgPath = filepath.Join(*dir, "etc", "agent.json")
	}
	if err := os.MkdirAll(filepath.Dir(o.cfgPath), 0o700); err != nil {
		return err
	}
	if err := os.Chmod(filepath.Dir(o.cfgPath), 0o700); err != nil {
		return err
	}

	bin := filepath.Join(*dir, "bc-agent")
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if filepath.Clean(self) != filepath.Clean(bin) {
		_ = systemctl("stop", linuxUnit) // eseguibile in uso: prima si ferma il servizio
		if err := copyFile(self, bin); err != nil {
			return fmt.Errorf("copia in %s: %w", bin, err)
		}
	}

	if _, statErr := os.Stat(o.cfgPath); statErr != nil || *reenroll {
		if o.token == "" {
			return errors.New("prima installazione: -server e -token sono obbligatori")
		}
		if err := enroll(o); err != nil {
			return err
		}
	} else {
		fmt.Println("registrazione esistente mantenuta:", o.cfgPath)
	}

	if err := os.WriteFile(filepath.Join(linuxUnitDir, linuxUnit), []byte(unitFile(bin, o.cfgPath)), 0o644); err != nil {
		return fmt.Errorf("unit systemd: %w", err)
	}
	ensure := filepath.Join(*dir, "ensure.sh")
	if err := os.WriteFile(ensure, []byte(ensureScript(bin, o.cfgPath)), 0o755); err != nil {
		return err
	}
	// systemd 219 (DSM 7) non ha "enable --now".
	for _, a := range [][]string{{"daemon-reload"}, {"enable", linuxUnit}, {"restart", linuxUnit}} {
		if err := systemctl(a...); err != nil {
			return err
		}
	}
	fmt.Printf("servizio %s installato e avviato (%s)\n", linuxUnit, bin)
	fmt.Printf("su Synology: in Pianificazione attività creare un'attività \"all'avvio\" come root che esegua %s\n", ensure)
	return nil
}

func uninstall(args []string) error {
	fs := flag.NewFlagSet("uninstall", flag.ExitOnError)
	dir := fs.String("dir", "/usr/local/bc-agent", "cartella di installazione")
	purge := fs.Bool("purge", false, "cancella anche configurazione e chiave (servirà un nuovo enrollment)")
	_ = fs.Parse(args)
	if os.Geteuid() != 0 {
		return errors.New("serve root")
	}
	_ = systemctl("stop", linuxUnit)
	_ = systemctl("disable", linuxUnit)
	_ = os.Remove(filepath.Join(linuxUnitDir, linuxUnit))
	_ = systemctl("daemon-reload")
	if *purge {
		return os.RemoveAll(*dir)
	}
	_ = os.Remove(filepath.Join(*dir, "bc-agent"))
	_ = os.Remove(filepath.Join(*dir, "ensure.sh"))
	fmt.Println("servizio rimosso; configurazione mantenuta in", filepath.Join(*dir, "etc"))
	return nil
}

func copyFile(src, dst string) error {
	b, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	tmp := dst + ".tmp"
	if err := os.WriteFile(tmp, b, 0o755); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}
