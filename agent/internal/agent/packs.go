package agent

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/collector/veeam"
	"backupconsole/agent/internal/pack"
	"backupconsole/agent/internal/upgrade"
)

// Pacchetto di raccolta: probe (script) firmati dalla CI, distribuiti dalla console.
// L'output grezzo va alla console, che lo interpreta: le modifiche alla raccolta non
// richiedono di aggiornare l'agente.
type packs struct {
	mu       sync.Mutex
	dir      string
	bundle   *pack.Bundle
	sha      string
	running  map[string]bool
	lastRun  map[string]time.Time
	env      pack.Env
	run      func(ctx context.Context, interpreter, script string) ([]byte, []byte, error)
	maxOut   int
	fetching bool
}

func newPacks(dataDir string) *packs {
	p := &packs{
		dir: dataDir, running: map[string]bool{}, lastRun: map[string]time.Time{}, maxOut: 8 << 20,
		env: pack.Env{ServiceInstalled: veeam.ServiceInstalled, Stat: os.Stat, ReadFile: os.ReadFile, Getenv: os.Getenv},
		run: runInterpreter,
	}
	if b, raw, _, err := pack.Load(dataDir, upgrade.ReleaseKey()); err == nil {
		p.bundle, p.sha = b, sha(raw)
		log.Printf("pacchetto di raccolta %s (dalla cache)", b.Version)
	} else if !os.IsNotExist(err) {
		log.Printf("pacchetto di raccolta in cache non valido: %v", err)
	}
	return p
}

func sha(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func runInterpreter(ctx context.Context, interpreter, script string) ([]byte, []byte, error) {
	switch interpreter {
	case "powershell":
		out, err := veeam.RunPowerShell(ctx, script)
		return out, nil, err
	case "sh":
		cmd := exec.CommandContext(ctx, "/bin/sh", "-s")
		cmd.Stdin = strings.NewReader(script)
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		err := cmd.Run()
		return stdout.Bytes(), stderr.Bytes(), err
	}
	return nil, nil, fmt.Errorf("interprete %q non supportato", interpreter)
}

// active: probe del pacchetto che si applicano a questa macchina.
func (p *packs) active() []pack.Probe {
	p.mu.Lock()
	b := p.bundle
	p.mu.Unlock()
	if b == nil {
		return nil
	}
	var out []pack.Probe
	for _, pr := range b.ForPlatform() {
		if pr.Detect.Eval(p.env) {
			out = append(out, pr)
		}
	}
	return out
}

// Roles e Replaces: ruoli rilevati dal pacchetto e collector integrati da non usare più.
func (p *packs) roles() []string {
	var out []string
	for _, pr := range p.active() {
		if pr.Role != "" {
			out = append(out, pr.Role)
		}
	}
	return out
}

func (p *packs) replaces(collector string) bool {
	for _, pr := range p.active() {
		if pr.Replaces == collector {
			return true
		}
	}
	return false
}

// sync: allinea il pacchetto a quello attivo in console (risposta all'heartbeat).
func (p *packs) sync(ctx context.Context, c *client.Client, ref *client.PackRef) {
	p.mu.Lock()
	if ref == nil || ref.SHA256 == p.sha || p.fetching {
		p.mu.Unlock()
		return
	}
	p.fetching = true
	p.mu.Unlock()
	defer func() { p.mu.Lock(); p.fetching = false; p.mu.Unlock() }()

	var d client.PackDownload
	if _, err := c.GetJSON(ctx, "/agent/v1/collector-pack/"+ref.Version, &d); err != nil {
		log.Printf("pacchetto di raccolta %s: download: %v", ref.Version, err)
		return
	}
	raw, err := base64.StdEncoding.DecodeString(d.Bundle)
	if err != nil {
		log.Printf("pacchetto di raccolta %s: %v", ref.Version, err)
		return
	}
	b, err := pack.Parse(raw, d.Signature, upgrade.ReleaseKey())
	if err != nil {
		// Firma non valida o formato non supportato: si resta sul pacchetto precedente.
		log.Printf("pacchetto di raccolta %s rifiutato: %v", ref.Version, err)
		return
	}
	if err := pack.Save(p.dir, raw, d.Signature); err != nil {
		log.Printf("pacchetto di raccolta: salvataggio: %v", err)
	}
	p.mu.Lock()
	old := "nessuno"
	if p.bundle != nil {
		old = p.bundle.Version
	}
	p.bundle, p.sha = b, sha(raw)
	p.lastRun = map[string]time.Time{} // i probe nuovi o cambiati girano subito
	p.mu.Unlock()
	log.Printf("pacchetto di raccolta %s → %s", old, b.Version)
}

type probeResult struct {
	Probe       string    `json:"probe"`
	PackVersion string    `json:"pack_version"`
	StartedAt   time.Time `json:"started_at"`
	DurationMS  int64     `json:"duration_ms"`
	OK          bool      `json:"ok"`
	Stdout      string    `json:"stdout"`
	Stderr      string    `json:"stderr,omitempty"`
	Error       string    `json:"error,omitempty"`
	Note        string    `json:"note,omitempty"`
	Truncated   bool      `json:"truncated,omitempty"`
}

// due: probe da eseguire adesso (intervallo trascorso e non già in corso).
func (p *packs) due(now time.Time) []pack.Probe {
	var out []pack.Probe
	for _, pr := range p.active() {
		iv := time.Duration(pr.IntervalS) * time.Second
		if iv < time.Minute {
			iv = 15 * time.Minute
		}
		p.mu.Lock()
		ok := !p.running[pr.ID] && now.Sub(p.lastRun[pr.ID]) >= iv
		if ok {
			p.running[pr.ID] = true
			p.lastRun[pr.ID] = now
		}
		p.mu.Unlock()
		if ok {
			out = append(out, pr)
		}
	}
	return out
}

func (p *packs) execute(ctx context.Context, pr pack.Probe) probeResult {
	p.mu.Lock()
	b := p.bundle
	p.mu.Unlock()
	res := probeResult{Probe: pr.ID, PackVersion: b.Version, StartedAt: time.Now().UTC()}
	timeout := time.Duration(pr.TimeoutS) * time.Second
	if timeout <= 0 {
		timeout = 5 * time.Minute
	}
	attempt := func(prelude string, t time.Duration) ([]byte, []byte, error) {
		cctx, cancel := context.WithTimeout(ctx, t)
		defer cancel()
		out, errOut, err := p.run(cctx, pr.Interpreter, b.Script(pr, prelude))
		if err != nil && cctx.Err() == context.DeadlineExceeded {
			err = fmt.Errorf("oltre %s: %w", t, err)
		}
		return out, errOut, err
	}
	out, errOut, err := attempt("", timeout)
	if err != nil && pr.Retry != nil && ctx.Err() == nil {
		log.Printf("probe %s: %v; secondo tentativo", pr.ID, err)
		first := err
		rt := time.Duration(pr.Retry.TimeoutS) * time.Second
		if rt <= 0 {
			rt = timeout
		}
		out, errOut, err = attempt(pr.Retry.Prelude, rt)
		if err == nil {
			res.Note = strings.TrimSpace(pr.Retry.Note + ": " + truncate(first.Error(), 500))
		}
	}
	res.DurationMS = time.Since(res.StartedAt).Milliseconds()
	res.OK = err == nil
	if err != nil {
		res.Error = truncate(err.Error(), 2000)
	}
	if len(out) > p.maxOut {
		out, res.Truncated = out[:p.maxOut], true
	}
	res.Stdout = string(out)
	res.Stderr = truncate(string(errOut), 8000)
	return res
}

// loop: ogni 30 secondi esegue i probe scaduti (al più due insieme) e invia l'output.
func (p *packs) loop(ctx context.Context, send func(ctx context.Context, r probeResult)) {
	slots := make(chan struct{}, 2)
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		for _, pr := range p.due(time.Now()) {
			select {
			case slots <- struct{}{}:
			case <-ctx.Done():
				return
			}
			go func(pr pack.Probe) {
				defer func() { <-slots; p.mu.Lock(); p.running[pr.ID] = false; p.mu.Unlock() }()
				send(ctx, p.execute(ctx, pr))
			}(pr)
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
