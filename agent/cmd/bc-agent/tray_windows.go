//go:build windows

package main

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows/registry"

	"backupconsole/agent/internal/trayembed"
)

const (
	trayExe     = "bc-agent-tray.exe"
	runKey      = `SOFTWARE\Microsoft\Windows\CurrentVersion\Run`
	runValue    = "BackupConsoleAgentTray"
	trayProcess = "bc-agent-tray.exe"
)

// ensureTray scrive (o aggiorna) bc-agent-tray.exe accanto a bc-agent.exe. L'icona in
// esecuzione tiene il file aperto: su Windows si può però rinominarlo, quindi lo
// spostiamo in .old e scriviamo il nuovo; l'icona si rilancia da sola al cambio versione.
func ensureTray(dir string) (string, error) {
	path := filepath.Join(dir, trayExe)
	if len(trayembed.Binary) == 0 {
		return "", nil // build senza icona
	}
	if cur, err := os.ReadFile(path); err == nil && sha256.Sum256(cur) == sha256.Sum256(trayembed.Binary) {
		return path, nil
	}
	old, _ := filepath.Glob(path + ".old-*")
	for _, f := range old {
		_ = os.Remove(f) // riesce solo se nessuna icona vecchia è ancora aperta
	}
	if _, err := os.Stat(path); err == nil {
		if err := os.Rename(path, fmt.Sprintf("%s.old-%d", path, time.Now().Unix())); err != nil {
			return "", err
		}
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, trayembed.Binary, 0o755); err != nil {
		return "", err
	}
	return path, os.Rename(tmp, path)
}

// registerTray fa partire l'icona a ogni accesso di qualsiasi utente.
func registerTray(path string) error {
	k, _, err := registry.CreateKey(registry.LOCAL_MACHINE, runKey, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	return k.SetStringValue(runValue, `"`+path+`"`)
}

func startTray(path string) {
	if err := exec.Command(path).Start(); err != nil {
		fmt.Println("attenzione: icona non avviata:", err)
	}
}

func installTray(dir string) {
	path, err := ensureTray(dir)
	if err != nil || path == "" {
		if err != nil {
			fmt.Println("attenzione: icona nella tray non installata:", err)
		}
		return
	}
	if err := registerTray(path); err != nil {
		fmt.Println("attenzione: avvio automatico dell'icona non registrato:", err)
		return
	}
	startTray(path)
	fmt.Println("icona nella tray installata (parte a ogni accesso degli utenti)")
}

func removeTray() {
	if k, err := registry.OpenKey(registry.LOCAL_MACHINE, runKey, registry.SET_VALUE); err == nil {
		_ = k.DeleteValue(runValue)
		k.Close()
	}
	var out bytes.Buffer
	cmd := exec.Command("taskkill", "/F", "/IM", trayProcess)
	cmd.Stdout, cmd.Stderr = &out, &out
	_ = cmd.Run() // nessuna icona aperta: errore atteso, ignorato
}

// syncTrayFromService: all'avvio del servizio riallinea l'icona alla versione
// dell'agente (dopo un aggiornamento remoto).
func syncTrayFromService() {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	if _, err := ensureTray(filepath.Dir(exe)); err != nil {
		log.Printf("aggiornamento icona tray: %v", err)
	}
}
