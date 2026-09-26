// Package agent contiene il ciclo principale: heartbeat, metriche e inventario.
package agent

import (
	"context"
	"errors"
	"log"
	"net/http"
	"time"

	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/collector"
	"backupconsole/agent/internal/collector/qnap"
	"backupconsole/agent/internal/collector/system"
	"backupconsole/agent/internal/config"
	"backupconsole/agent/internal/version"
)

// Detect ritorna i collector attivi su questa macchina.
func Detect() []collector.Collector {
	all := []collector.Collector{qnap.New(), system.New()}
	var out []collector.Collector
	for _, c := range all {
		if c.Detect() {
			out = append(out, c)
		}
	}
	return out
}

func Roles(cs []collector.Collector) []string {
	roles := make([]string, 0, len(cs))
	for _, c := range cs {
		roles = append(roles, c.Role())
	}
	return roles
}

func CollectInventory(ctx context.Context, cs []collector.Collector) collector.Inventory {
	parts := map[string]collector.Inventory{}
	for _, c := range cs {
		inv, err := c.Inventory(ctx)
		if err != nil {
			log.Printf("inventario %s: %v", c.Name(), err)
		}
		parts[c.Name()] = inv
	}
	return collector.Merge(parts)
}

func CollectMetrics(ctx context.Context, cs []collector.Collector) []collector.Metric {
	var out []collector.Metric
	for _, c := range cs {
		ms, err := c.Metrics(ctx)
		if err != nil {
			log.Printf("metriche %s: %v", c.Name(), err)
		}
		out = append(out, ms...)
	}
	return out
}

type Agent struct {
	cfg        *config.Config
	client     *client.Client
	collectors []collector.Collector
	started    time.Time
	status     string
	approved   bool
}

func New(cfg *config.Config, c *client.Client) *Agent {
	return &Agent{cfg: cfg, client: c, collectors: Detect(), started: time.Now()}
}

// Run gira fino alla cancellazione del contesto.
// TODO: buffer locale su disco per i dati raccolti mentre la console non è raggiungibile.
func (a *Agent) Run(ctx context.Context) error {
	log.Printf("bc-agent %s avviato, ruoli %v, console %s", version.Version, Roles(a.collectors), a.cfg.ServerURL)

	hb := time.NewTicker(time.Duration(a.cfg.HeartbeatSec) * time.Second)
	mt := time.NewTicker(time.Duration(a.cfg.MetricsSec) * time.Second)
	inv := time.NewTicker(time.Duration(a.cfg.InventorySec) * time.Second)
	defer hb.Stop()
	defer mt.Stop()
	defer inv.Stop()

	a.heartbeat(ctx)
	if a.approved {
		a.sendInventory(ctx)
		a.sendMetrics(ctx)
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-hb.C:
			wasApproved := a.approved
			a.heartbeat(ctx)
			if a.approved && !wasApproved {
				// Appena approvato: non aspettiamo il prossimo giro.
				a.sendInventory(ctx)
				a.sendMetrics(ctx)
			}
		case <-mt.C:
			if a.approved {
				a.sendMetrics(ctx)
			}
		case <-inv.C:
			if a.approved {
				a.sendInventory(ctx)
			}
		}
	}
}

func (a *Agent) heartbeat(ctx context.Context) {
	var resp client.HeartbeatResponse
	err := a.client.Post(ctx, "/agent/v1/heartbeat", client.HeartbeatRequest{
		Version:      version.Version,
		Roles:        Roles(a.collectors),
		Capabilities: map[string]any{"collectors": Roles(a.collectors)},
		AllowShell:   a.cfg.AllowShell,
		UptimeS:      int64(time.Since(a.started).Seconds()),
	}, &resp)
	if err != nil {
		var he *client.HTTPError
		if errors.As(err, &he) && he.Status == http.StatusForbidden {
			log.Printf("heartbeat rifiutato: agente revocato? (%v)", err)
		} else {
			log.Printf("heartbeat: %v", err)
		}
		return
	}
	if resp.Status != a.status {
		log.Printf("stato agente: %s", resp.Status)
		a.status = resp.Status
	}
	a.approved = resp.Status == "approved"
}

func (a *Agent) sendMetrics(ctx context.Context) {
	ms := CollectMetrics(ctx, a.collectors)
	if len(ms) == 0 {
		return
	}
	env := a.client.NewEnvelope(map[string]any{"metrics": ms})
	if err := a.client.Send(ctx, "/agent/v1/metrics", env); err != nil {
		log.Printf("invio metriche: %v", err)
	}
}

func (a *Agent) sendInventory(ctx context.Context) {
	env := a.client.NewEnvelope(CollectInventory(ctx, a.collectors))
	if err := a.client.Send(ctx, "/agent/v1/inventory", env); err != nil {
		log.Printf("invio inventario: %v", err)
	}
}
