// Package pack: pacchetti di raccolta. La logica di raccolta (script PowerShell o sh e
// quando eseguirli) non è nel binario dell'agente ma in un pacchetto che la console
// distribuisce; si aggiorna senza aggiornare l'agente.
//
// Il pacchetto è firmato dalla CI con la stessa chiave dei rilasci dell'agente e l'agente
// esegue solo pacchetti con firma valida (chiave pubblica incorporata in build): la
// console da sola non può far eseguire codice ai dispositivi.
package pack

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
)

// Format: versione del formato del pacchetto che questo agente sa leggere.
const Format = 1

type Retry struct {
	// Premessa aggiunta allo script per il secondo tentativo (es. raccolta di base).
	Prelude  string `json:"prelude"`
	TimeoutS int    `json:"timeout_s"`
	Note     string `json:"note"` // errore riportato quando si ripiega sul secondo tentativo
}

type Probe struct {
	ID          string `json:"id"`
	Platform    string `json:"platform"`    // windows / linux / "" (tutte)
	Interpreter string `json:"interpreter"` // powershell / sh
	// Script concatenati nell'ordine (es. funzioni comuni + script del prodotto).
	Scripts   []string `json:"scripts"`
	Detect    *Cond    `json:"detect,omitempty"`
	Role      string   `json:"role,omitempty"`     // ruolo riportato alla console se rilevato
	Replaces  string   `json:"replaces,omitempty"` // collector integrato che il probe sostituisce
	IntervalS int      `json:"interval_s"`
	TimeoutS  int      `json:"timeout_s"`
	Retry     *Retry   `json:"retry,omitempty"`
}

type Bundle struct {
	Format  int               `json:"format"`
	Version string            `json:"version"`
	Probes  []Probe           `json:"probes"`
	Files   map[string]string `json:"files"`
}

var versionRe = regexp.MustCompile(`^\d+\.\d+\.\d+$`)
var idRe = regexp.MustCompile(`^[a-z0-9_]{1,40}$`)

// Parse verifica la firma (Ed25519 sui byte esatti del pacchetto) e ne controlla il contenuto.
func Parse(raw []byte, signature string, pub ed25519.PublicKey) (*Bundle, error) {
	if pub == nil {
		return nil, errors.New("agente senza chiave di verifica: pacchetti di raccolta disabilitati")
	}
	sig, err := base64.StdEncoding.DecodeString(signature)
	if err != nil || !ed25519.Verify(pub, raw, sig) {
		return nil, errors.New("firma del pacchetto di raccolta non valida")
	}
	var b Bundle
	if err := json.Unmarshal(raw, &b); err != nil {
		return nil, fmt.Errorf("pacchetto di raccolta non leggibile: %w", err)
	}
	if b.Format != Format {
		return nil, fmt.Errorf("formato del pacchetto %d non supportato (questo agente legge il %d)", b.Format, Format)
	}
	if !versionRe.MatchString(b.Version) {
		return nil, fmt.Errorf("versione del pacchetto %q non valida", b.Version)
	}
	for _, p := range b.Probes {
		if !idRe.MatchString(p.ID) {
			return nil, fmt.Errorf("id di probe %q non valido", p.ID)
		}
		if p.Interpreter != "powershell" && p.Interpreter != "sh" {
			return nil, fmt.Errorf("probe %s: interprete %q non supportato", p.ID, p.Interpreter)
		}
		for _, f := range p.Scripts {
			if _, ok := b.Files[f]; !ok {
				return nil, fmt.Errorf("probe %s: file %s mancante", p.ID, f)
			}
		}
	}
	return &b, nil
}

// Script completo di un probe: eventuale premessa + file nell'ordine.
func (b *Bundle) Script(p Probe, prelude string) string {
	var sb strings.Builder
	if prelude != "" {
		sb.WriteString(prelude)
		sb.WriteString("\n")
	}
	for _, f := range p.Scripts {
		sb.WriteString(b.Files[f])
		sb.WriteString("\n")
	}
	return sb.String()
}

// ForPlatform: probe per questo sistema operativo.
func (b *Bundle) ForPlatform() []Probe {
	var out []Probe
	for _, p := range b.Probes {
		if p.Platform == "" || p.Platform == runtime.GOOS {
			out = append(out, p)
		}
	}
	return out
}

// --- cache su disco ------------------------------------------------------------

type cached struct {
	Bundle    string `json:"bundle"` // byte originali (base64): la firma si riverifica al caricamento
	Signature string `json:"signature"`
}

func Save(dir string, raw []byte, signature string) error {
	b, _ := json.Marshal(cached{Bundle: base64.StdEncoding.EncodeToString(raw), Signature: signature})
	p := filepath.Join(dir, "collector-pack.json")
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

func Load(dir string, pub ed25519.PublicKey) (*Bundle, []byte, string, error) {
	b, err := os.ReadFile(filepath.Join(dir, "collector-pack.json"))
	if err != nil {
		return nil, nil, "", err
	}
	var c cached
	if err := json.Unmarshal(b, &c); err != nil {
		return nil, nil, "", err
	}
	raw, err := base64.StdEncoding.DecodeString(c.Bundle)
	if err != nil {
		return nil, nil, "", err
	}
	bundle, err := Parse(raw, c.Signature, pub)
	return bundle, raw, c.Signature, err
}
