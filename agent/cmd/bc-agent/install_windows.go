//go:build windows

package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"backupconsole/agent/internal/config"
	"backupconsole/agent/internal/winsvc"
)

func installDir() string {
	base := os.Getenv("ProgramFiles")
	if base == "" {
		base = `C:\Program Files`
	}
	return filepath.Join(base, "BackupConsoleAgent")
}

// install copia l'eseguibile in Program Files, registra l'agente e crea il servizio.
// Rilanciato su una macchina già installata aggiorna il binario e riavvia il servizio
// (senza -token non rifà l'enrollment).
func install(args []string) error {
	fs := flag.NewFlagSet("install", flag.ExitOnError)
	o := enrollFlags(fs)
	_ = fs.Parse(args)

	dataDir := filepath.Dir(o.cfgPath)
	if err := os.MkdirAll(dataDir, 0o700); err != nil {
		return err
	}
	// ProgramData eredita la lettura per "Users": la chiave privata non deve essere leggibile.
	// S-1-5-18 = LocalSystem, S-1-5-32-544 = Administrators.
	if out, err := exec.Command("icacls", dataDir, "/inheritance:r",
		"/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F").CombinedOutput(); err != nil {
		return fmt.Errorf("permessi su %s: %v: %s", dataDir, err, out)
	}

	_ = winsvc.Stop()
	target := filepath.Join(installDir(), "bc-agent.exe")
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if !strings.EqualFold(filepath.Clean(self), filepath.Clean(target)) {
		if err := copyFile(self, target); err != nil {
			return fmt.Errorf("copia in %s: %w", target, err)
		}
	}

	if _, err := os.Stat(o.cfgPath); err != nil || o.token != "" {
		if o.token == "" {
			return errors.New("prima installazione: -server e -token sono obbligatori")
		}
		if err := enroll(o); err != nil {
			return err
		}
	} else {
		fmt.Println("registrazione esistente mantenuta:", o.cfgPath)
	}

	if err := winsvc.Install(target, "run", "-config", o.cfgPath); err != nil {
		return fmt.Errorf("creazione servizio: %w", err)
	}
	if err := winsvc.Start(); err != nil {
		return fmt.Errorf("avvio servizio: %w", err)
	}
	fmt.Printf("servizio %s installato e avviato (%s)\n", winsvc.Name, target)
	return nil
}

func uninstall(args []string) error {
	fs := flag.NewFlagSet("uninstall", flag.ExitOnError)
	purge := fs.Bool("purge", false, "rimuove anche configurazione, chiave e log in ProgramData")
	_ = fs.Parse(args)
	if err := winsvc.Stop(); err != nil {
		return err
	}
	if err := winsvc.Remove(); err != nil {
		return err
	}
	// L'eseguibile in uso non si può cancellare: se stiamo girando da Program Files
	// lasciamo la directory (verrà rimossa al prossimo riavvio o a mano).
	if err := os.RemoveAll(installDir()); err != nil {
		fmt.Println("attenzione:", err)
	}
	if *purge {
		if err := os.RemoveAll(filepath.Dir(config.DefaultPath())); err != nil {
			return err
		}
	}
	fmt.Println("servizio rimosso. Ricordarsi di revocare il dispositivo dalla console.")
	return nil
}

func copyFile(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".tmp"
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}
