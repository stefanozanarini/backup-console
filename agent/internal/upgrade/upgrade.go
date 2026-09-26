// Package upgrade applica gli aggiornamenti dell'agente richiesti dalla console.
//
// Sequenza:
//  1. download del binario nella stessa directory dell'eseguibile (<exe>.new);
//  2. verifica di dimensione, SHA-256 e firma Ed25519 con la chiave pubblica
//     incorporata in build (PublicKey): la console da sola non può far eseguire
//     codice arbitrario;
//  3. prova del nuovo binario (`<exe>.new version` deve stampare la versione attesa):
//     intercetta architetture sbagliate o file corrotti prima di toccare quello attuale;
//  4. marker di aggiornamento + swap: <exe> → <exe>.prev, <exe>.new → <exe>
//     (anche su Windows un eseguibile in uso si può rinominare);
//  5. l'agente termina con ExitRestart e il supervisore (QPKG o servizio Windows)
//     lo riavvia con la nuova versione.
//
// Rollback: il marker conta gli avvii della nuova versione; se non arriva a un
// heartbeat approvato entro MaxAttempts avvii, al successivo si ripristina <exe>.prev.
package upgrade

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// PublicKey (base64, 32 byte) è impostata in build:
// -ldflags "-X backupconsole/agent/internal/upgrade.PublicKey=..."
var PublicKey = ""

// ExitRestart è il codice di uscita che chiede al supervisore un riavvio immediato.
const ExitRestart = 75

const (
	MaxAttempts = 3
	MaxSize     = 200 << 20
	markerName  = "upgrade.json"
)

var ErrRestart = errors.New("riavvio richiesto")

// Platform identifica l'asset da scaricare; coincide con app.models.releases.PLATFORMS.
func Platform() string {
	arch := runtime.GOARCH
	if arch == "arm" {
		arch = "armv7"
	}
	return runtime.GOOS + "-" + arch
}

// executable è sostituibile nei test.
var executable = os.Executable

type Request struct {
	Version   string `json:"version"`
	Platform  string `json:"platform"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
	Signature string `json:"signature"`
	Path      string `json:"path"`
	From      string `json:"from"`
}

func ParseRequest(payload map[string]any) (Request, error) {
	var r Request
	b, _ := json.Marshal(payload)
	if err := json.Unmarshal(b, &r); err != nil {
		return r, err
	}
	if r.Version == "" || r.SHA256 == "" || r.Path == "" {
		return r, errors.New("richiesta di aggiornamento incompleta")
	}
	return r, nil
}

type Downloader interface {
	Download(ctx context.Context, path string, w io.Writer, maxBytes int64) (int64, error)
}

type Options struct {
	CurrentVersion string
	DataDir        string
	AllowUnsigned  bool
	Log            func(format string, args ...any)
}

// Apply scarica, verifica e installa. Se ritorna nil l'agente deve terminare con ExitRestart.
func Apply(ctx context.Context, dl Downloader, req Request, opt Options) error {
	logf := opt.Log
	if logf == nil {
		logf = func(string, ...any) {}
	}
	if req.Platform != "" && req.Platform != Platform() {
		return fmt.Errorf("piattaforma %s diversa da quella locale %s", req.Platform, Platform())
	}
	if req.Version == opt.CurrentVersion {
		return fmt.Errorf("versione %s già installata", req.Version)
	}
	pub, err := publicKey(opt.AllowUnsigned)
	if err != nil {
		return err
	}
	exe, err := executable()
	if err != nil {
		return err
	}
	if exe, err = filepath.EvalSymlinks(exe); err != nil {
		return err
	}
	newPath, prevPath := exe+".new", exe+".prev"
	_ = os.Remove(newPath)

	f, err := os.OpenFile(newPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		return fmt.Errorf("creazione %s: %w", newPath, err)
	}
	h := sha256.New()
	n, err := dl.Download(ctx, req.Path, io.MultiWriter(f, h), MaxSize)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	cleanup := func() { _ = os.Remove(newPath) }
	if err != nil {
		cleanup()
		return fmt.Errorf("download: %w", err)
	}
	logf("scaricati %d byte", n)
	if req.Size > 0 && n != req.Size {
		cleanup()
		return fmt.Errorf("dimensione %d diversa da quella attesa %d", n, req.Size)
	}
	if got := hex.EncodeToString(h.Sum(nil)); !strings.EqualFold(got, req.SHA256) {
		cleanup()
		return fmt.Errorf("sha256 %s diverso da quello atteso", got)
	}
	if pub != nil {
		data, err := os.ReadFile(newPath)
		if err != nil {
			cleanup()
			return err
		}
		sig, err := base64.StdEncoding.DecodeString(req.Signature)
		if err != nil || !ed25519.Verify(pub, data, sig) {
			cleanup()
			return errors.New("firma del binario non valida")
		}
		logf("firma verificata")
	} else {
		logf("ATTENZIONE: firma non verificata (allow_unsigned_upgrades)")
	}

	if err := preflight(ctx, newPath, req.Version); err != nil {
		cleanup()
		return err
	}
	logf("prova del nuovo binario riuscita")

	if err := writeMarker(opt.DataDir, marker{From: opt.CurrentVersion, To: req.Version, Exe: exe}); err != nil {
		cleanup()
		return err
	}
	_ = os.Remove(prevPath)
	if err := os.Rename(exe, prevPath); err != nil {
		cleanup()
		removeMarker(opt.DataDir)
		return fmt.Errorf("rinomina dell'eseguibile attuale: %w", err)
	}
	if err := os.Rename(newPath, exe); err != nil {
		_ = os.Rename(prevPath, exe)
		cleanup()
		removeMarker(opt.DataDir)
		return fmt.Errorf("installazione del nuovo eseguibile: %w", err)
	}
	logf("installata la versione %s, riavvio", req.Version)
	return nil
}

func publicKey(allowUnsigned bool) (ed25519.PublicKey, error) {
	if PublicKey == "" {
		if allowUnsigned {
			return nil, nil
		}
		return nil, errors.New("questo binario non ha una chiave di verifica: aggiornamenti remoti disabilitati")
	}
	b, err := base64.StdEncoding.DecodeString(PublicKey)
	if err != nil || len(b) != ed25519.PublicKeySize {
		return nil, errors.New("chiave pubblica di verifica non valida")
	}
	return ed25519.PublicKey(b), nil
}

func preflight(ctx context.Context, path, want string) error {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, path, "version").Output()
	if err != nil {
		return fmt.Errorf("il nuovo binario non si avvia su questo sistema: %w", err)
	}
	if got := strings.TrimSpace(string(out)); got != want {
		return fmt.Errorf("il nuovo binario riporta la versione %q invece di %q", got, want)
	}
	return nil
}

// --- marker e rollback ------------------------------------------------------

type marker struct {
	From       string `json:"from"`
	To         string `json:"to"`
	Exe        string `json:"exe"`
	Attempts   int    `json:"attempts"`
	RolledBack bool   `json:"rolled_back"`
}

func markerPath(dir string) string { return filepath.Join(dir, markerName) }

func writeMarker(dir string, m marker) error {
	b, _ := json.Marshal(m)
	tmp := markerPath(dir) + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, markerPath(dir))
}

func readMarker(dir string) (marker, bool) {
	var m marker
	b, err := os.ReadFile(markerPath(dir))
	if err != nil {
		return m, false
	}
	return m, json.Unmarshal(b, &m) == nil
}

func removeMarker(dir string) { _ = os.Remove(markerPath(dir)) }

// Outcome è l'esito di un aggiornamento precedente, da notificare alla console.
type Outcome struct {
	Type string // agent.upgraded / agent.upgrade_rollback
	From string
	To   string
}

// OnStartup va chiamata all'avvio. Ritorna ErrRestart se ha appena ripristinato la
// versione precedente (l'agente deve uscire con ExitRestart).
func OnStartup(dataDir, running string, logf func(string, ...any)) error {
	m, ok := readMarker(dataDir)
	if !ok || m.RolledBack || running != m.To {
		return nil
	}
	m.Attempts++
	if m.Attempts <= MaxAttempts {
		return writeMarker(dataDir, m)
	}
	prev := m.Exe + ".prev"
	if _, err := os.Stat(prev); err != nil {
		logf("la versione %s non si stabilizza ma %s non esiste: nessun rollback possibile", m.To, prev)
		removeMarker(dataDir)
		return nil
	}
	logf("la versione %s non ha completato %d avvii: ripristino %s", m.To, MaxAttempts, m.From)
	failed := m.Exe + ".failed"
	_ = os.Remove(failed)
	if err := os.Rename(m.Exe, failed); err != nil {
		return fmt.Errorf("rollback: %w", err)
	}
	if err := os.Rename(prev, m.Exe); err != nil {
		_ = os.Rename(failed, m.Exe)
		return fmt.Errorf("rollback: %w", err)
	}
	m.RolledBack = true
	if err := writeMarker(dataDir, m); err != nil {
		return err
	}
	return ErrRestart
}

// Pending: la versione in esecuzione è appena stata installata e non ha ancora
// completato un heartbeat approvato.
func Pending(dataDir, running string) bool {
	m, ok := readMarker(dataDir)
	return ok && !m.RolledBack && m.To == running
}

// OnHealthy va chiamata dopo il primo heartbeat approvato: chiude il marker e dice
// alla console com'è andata.
func OnHealthy(dataDir, running string) *Outcome {
	m, ok := readMarker(dataDir)
	if !ok {
		return nil
	}
	removeMarker(dataDir)
	switch {
	case m.RolledBack:
		return &Outcome{Type: "agent.upgrade_rollback", From: m.From, To: m.To}
	case running == m.To:
		return &Outcome{Type: "agent.upgraded", From: m.From, To: m.To}
	}
	return nil
}
