// Package veeam raccoglie lo stato dei job da Veeam Backup & Replication e da
// Veeam Agent for Windows tramite gli script PowerShell incorporati.
package veeam

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"backupconsole/agent/internal/collector"
)

//go:embed scripts/common.ps1
var commonPS string

//go:embed scripts/vbr.ps1
var vbrPS string

//go:embed scripts/vaw.ps1
var vawPS string

type Job struct {
	ID               string         `json:"id"`
	Name             string         `json:"name"`
	Type             string         `json:"type,omitempty"`
	Enabled          bool           `json:"enabled"`
	LastResult       string         `json:"last_result,omitempty"`
	LastState        string         `json:"last_state,omitempty"`
	LastStart        *time.Time     `json:"last_start,omitempty"`
	LastEnd          *time.Time     `json:"last_end,omitempty"`
	LastSuccess      *time.Time     `json:"last_success,omitempty"`
	TransferredBytes *int64         `json:"transferred_bytes,omitempty"`
	NextRun          *time.Time     `json:"next_run,omitempty"`
	Details          map[string]any `json:"details,omitempty"`
}

type Repository struct {
	Name       string `json:"name"`
	Type       string `json:"type,omitempty"`
	TotalBytes *int64 `json:"total_bytes,omitempty"`
	FreeBytes  *int64 `json:"free_bytes,omitempty"`
}

type License struct {
	Edition           string     `json:"edition,omitempty"`
	Status            string     `json:"status,omitempty"`
	Expiration        *time.Time `json:"expiration,omitempty"`
	SupportExpiration *time.Time `json:"support_expiration,omitempty"`
}

type Payload struct {
	Product      string       `json:"product"`
	Version      string       `json:"version,omitempty"`
	Jobs         []Job        `json:"jobs"`
	Repositories []Repository `json:"repositories"`
	License      *License     `json:"license,omitempty"`
	Errors       []string     `json:"errors"`
}

// Parse legge l'output JSON degli script e normalizza gli esiti.
func Parse(out []byte) (Payload, error) {
	var p Payload
	// PowerShell può anteporre un BOM o righe di avviso: partiamo dalla prima "{".
	if i := strings.IndexByte(string(out), '{'); i > 0 {
		out = out[i:]
	}
	if err := json.Unmarshal(out, &p); err != nil {
		return p, fmt.Errorf("output dello script non valido: %w", err)
	}
	for i := range p.Jobs {
		p.Jobs[i].LastResult = normalizeResult(p.Jobs[i].LastResult)
	}
	if p.Jobs == nil {
		p.Jobs = []Job{}
	}
	if p.Repositories == nil {
		p.Repositories = []Repository{}
	}
	if p.Errors == nil {
		p.Errors = []string{}
	}
	return p, nil
}

func normalizeResult(r string) string {
	switch strings.ToLower(strings.TrimSpace(r)) {
	case "success", "succeeded", "ok":
		return "Success"
	case "warning":
		return "Warning"
	case "failed", "failure", "error":
		return "Failed"
	case "", "none":
		return "None"
	}
	return r
}

type runner func(ctx context.Context, script string) ([]byte, error)

// Collector: uno per prodotto (vbr / vaw).
type Collector struct {
	product string
	role    string
	script  string
	detect  func() bool
	run     runner

	mu   sync.Mutex
	last *Payload
}

func NewVBR() *Collector {
	return &Collector{product: "vbr", role: "veeam_vbr", script: vbrPS, detect: detectVBR, run: runPowerShell}
}

func NewVAW() *Collector {
	return &Collector{product: "vaw", role: "veeam_agent", script: vawPS, detect: detectVAW, run: runPowerShell}
}

func (c *Collector) Name() string { return "veeam_" + c.product }
func (c *Collector) Role() string { return c.role }
func (c *Collector) Detect() bool { return c.detect() }

// Inventory riporta l'ultimo stato noto (senza rilanciare PowerShell).
func (c *Collector) Inventory(context.Context) (collector.Inventory, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	data := map[string]any{"product": c.product}
	if c.last != nil {
		data["version"] = c.last.Version
		data["jobs"] = len(c.last.Jobs)
	}
	return collector.Inventory{Data: data}, nil
}

func (c *Collector) Metrics(context.Context) ([]collector.Metric, error) { return nil, nil }

// Tempi massimi della raccolta: quella completa (con lo storico delle sessioni, che su
// server B&R con molti job di copia può essere lenta) e quella di base, che parte se la
// prima non riesce, così esiti, repository e licenza arrivano comunque in console.
var (
	fullTimeout  = 4 * time.Minute
	basicTimeout = 5 * time.Minute
)

// Report esegue lo script e ritorna il payload per /agent/v1/veeam.
func (c *Collector) Report(ctx context.Context) (string, any, error) {
	p, err := c.collect(ctx, fullTimeout, "")
	if err != nil && c.product == "vbr" && ctx.Err() == nil {
		log.Printf("veeam: raccolta completa non riuscita, ripiego sulla raccolta di base: %v", err)
		var errBasic error
		p, errBasic = c.collect(ctx, basicTimeout, "$IncludeHistory = $false\n")
		if errBasic != nil {
			return "", nil, fmt.Errorf("%v; anche la raccolta di base: %w", err, errBasic)
		}
		p.Errors = append(p.Errors, "storico sessioni non raccolto: "+truncate(err.Error(), 500))
		err = nil
	}
	if err != nil {
		return "", nil, err
	}
	c.mu.Lock()
	c.last = &p
	c.mu.Unlock()
	return "/agent/v1/veeam", p, nil
}

func (c *Collector) collect(ctx context.Context, timeout time.Duration, prelude string) (Payload, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	out, err := c.run(ctx, commonPS+"\n"+prelude+c.script)
	if err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return Payload{}, fmt.Errorf("script Veeam oltre %s: %w", timeout, err)
		}
		return Payload{}, err
	}
	p, err := Parse(out)
	if err != nil {
		return Payload{}, err
	}
	p.Product = c.product
	return p, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
