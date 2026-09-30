// Package status è il file di stato che l'agente (servizio) scrive e che l'icona nella
// tray di Windows legge. Contiene solo informazioni non sensibili: sta in una cartella
// leggibile dagli utenti, separata da quella con chiave e configurazione.
package status

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

type Status struct {
	Version     string    `json:"version"`
	State       string    `json:"state"` // approved / pending / revoked / unreachable
	Server      string    `json:"server"`
	Device      string    `json:"device"`
	RemoteExec  bool      `json:"remote_exec"`
	LastContact time.Time `json:"last_contact,omitempty"`
	LastError   string    `json:"last_error,omitempty"`
	UpdatedAt   time.Time `json:"updated_at"`
	// Finestra di backup: in corso (solo Until) o in arrivo (Start e Until, nel periodo di
	// promemoria). Nil se nessuna delle due: l'icona lo mostra all'utente.
	BackupWindowStart *time.Time `json:"backup_window_start,omitempty"`
	BackupWindowUntil *time.Time `json:"backup_window_until,omitempty"`
}

// Path del file di stato; vuoto dove non serve (fuori da Windows non c'è l'icona).
func Path() string {
	if runtime.GOOS != "windows" {
		return ""
	}
	base := os.Getenv("ProgramData")
	if base == "" {
		base = `C:\ProgramData`
	}
	return filepath.Join(base, "BackupConsoleAgentStatus", "status.json")
}

func Write(path string, s Status) error {
	if path == "" {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(s, "", "  ")
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func Read(path string) (Status, error) {
	var s Status
	b, err := os.ReadFile(path)
	if err != nil {
		return s, err
	}
	return s, json.Unmarshal(b, &s)
}
