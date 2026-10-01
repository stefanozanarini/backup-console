// Package agent contiene il ciclo principale: heartbeat, metriche, inventario, report
// Veeam e comandi dalla console (esecuzione remota e aggiornamenti).
package agent

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/collector"
	"backupconsole/agent/internal/collector/qnap"
	"backupconsole/agent/internal/collector/synology"
	"backupconsole/agent/internal/collector/system"
	"backupconsole/agent/internal/collector/veeam"
	"backupconsole/agent/internal/config"
	"backupconsole/agent/internal/status"
	"backupconsole/agent/internal/upgrade"
	"backupconsole/agent/internal/version"
)

// ErrRestart: Run è terminato per applicare un aggiornamento; il processo deve uscire
// con upgrade.ExitRestart perché il supervisore lo rilanci.
var ErrRestart = upgrade.ErrRestart

// Reporter è implementato dai collector che inviano un report dedicato (es. Veeam).
type Reporter interface {
	Report(ctx context.Context) (endpoint string, payload any, err error)
}

// Detect ritorna i collector attivi su questa macchina.
func Detect() []collector.Collector {
	all := []collector.Collector{qnap.New(), synology.New(), veeam.NewVBR(), veeam.NewVAW(), veeam.NewUranium(), system.New()}
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
	lastOK     time.Time
	approved   atomic.Bool
	healthy    sync.Once
	wasHealthy atomic.Bool

	restartOnce sync.Once
	restart     chan struct{}
	execSlots   chan struct{}
	window      *backupWindow
	packs       *packs
}

func New(cfg *config.Config, c *client.Client) *Agent {
	return &Agent{
		cfg: cfg, client: c, collectors: Detect(), started: time.Now(),
		restart: make(chan struct{}), execSlots: make(chan struct{}, 2),
		window: newBackupWindow(cfg.DataDir),
		packs:  newPacks(cfg.DataDir),
	}
}

func (a *Agent) requestRestart() { a.restartOnce.Do(func() { close(a.restart) }) }

// Run gira fino alla cancellazione del contesto o a un aggiornamento (ErrRestart).
// TODO: buffer locale su disco per i dati raccolti mentre la console non è raggiungibile.
func (a *Agent) Run(ctx context.Context) error {
	if err := upgrade.OnStartup(a.cfg.DataDir, version.Version, log.Printf); err != nil {
		return err
	}
	syncQPKGVersion(version.Version, execCfg, log.Printf)
	log.Printf("bc-agent %s (%s) avviato, ruoli %v, console %s, esecuzione remota %s",
		version.Version, upgrade.Platform(), Roles(a.collectors), a.cfg.ServerURL, onOff(a.cfg.RemoteExecAllowed()))

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var wg sync.WaitGroup
	wg.Add(3)
	go func() {
		defer wg.Done()
		a.commandLoop(ctx)
	}()
	go func() {
		defer wg.Done()
		a.packs.loop(ctx, a.sendProbe)
	}()
	go func() {
		defer wg.Done()
		a.window.loop(ctx)
	}()

	hb := time.NewTicker(time.Duration(a.cfg.HeartbeatSec) * time.Second)
	mt := time.NewTicker(time.Duration(a.cfg.MetricsSec) * time.Second)
	inv := time.NewTicker(time.Duration(a.cfg.InventorySec) * time.Second)
	vr := time.NewTicker(time.Duration(a.cfg.VeeamSec) * time.Second)
	// Dopo un aggiornamento: se la nuova versione non arriva a un heartbeat approvato
	// usciamo con errore, così il supervisore la riavvia e dopo MaxAttempts avvii
	// scatta il rollback.
	var watchdog <-chan time.Time
	if upgrade.Pending(a.cfg.DataDir, version.Version) {
		t := time.NewTimer(upgradeHealthTimeout())
		defer t.Stop()
		watchdog = t.C
	}
	defer hb.Stop()
	defer mt.Stop()
	defer inv.Stop()
	defer vr.Stop()

	collectAll := func() {
		a.sendInventory(ctx)
		a.sendMetrics(ctx)
		a.sendReports(ctx)
	}
	a.heartbeat(ctx)
	if a.approved.Load() {
		collectAll()
	}
	for {
		select {
		case <-ctx.Done():
			wg.Wait()
			return nil
		case <-a.restart:
			cancel()
			wg.Wait()
			return ErrRestart
		case <-watchdog:
			if !a.wasHealthy.Load() {
				cancel()
				wg.Wait()
				return fmt.Errorf("versione %s: nessun heartbeat approvato entro %s dall'aggiornamento", version.Version, upgradeHealthTimeout())
			}
		case <-hb.C:
			was := a.approved.Load()
			a.heartbeat(ctx)
			if a.approved.Load() && !was {
				// Appena approvato: non aspettiamo il prossimo giro.
				collectAll()
			}
		case <-mt.C:
			if a.approved.Load() {
				a.sendMetrics(ctx)
			}
		case <-inv.C:
			if a.approved.Load() {
				a.sendInventory(ctx)
			}
		case <-vr.C:
			if a.approved.Load() {
				a.sendReports(ctx)
			}
		}
	}
}

func upgradeHealthTimeout() time.Duration {
	if s, err := strconv.Atoi(os.Getenv("BC_UPGRADE_HEALTH_TIMEOUT")); err == nil && s > 0 {
		return time.Duration(s) * time.Second
	}
	return 10 * time.Minute
}

func onOff(b bool) string {
	if b {
		return "attiva"
	}
	return "disattivata"
}

func (a *Agent) heartbeat(ctx context.Context) {
	var resp client.HeartbeatResponse
	err := a.client.Post(ctx, "/agent/v1/heartbeat", client.HeartbeatRequest{
		Version:      version.Version,
		Roles:        a.roles(),
		Capabilities: map[string]any{"collectors": Roles(a.collectors), "platform": upgrade.Platform(), "upgrade": upgrade.PublicKey != "" || a.cfg.AllowUnsignedUpgrades},
		AllowShell:   a.cfg.RemoteExecAllowed(),
		UptimeS:      int64(time.Since(a.started).Seconds()),
	}, &resp)
	if err != nil {
		var he *client.HTTPError
		state := "unreachable"
		if errors.As(err, &he) && he.Status == http.StatusForbidden {
			log.Printf("heartbeat rifiutato: agente revocato? (%v)", err)
			state = "revoked"
		} else {
			log.Printf("heartbeat: %v", err)
		}
		a.writeStatus(state, err)
		return
	}
	a.lastOK = time.Now()
	a.window.set(resp.BackupWindow)
	if a.approved.Load() || resp.Status == "approved" {
		go a.packs.sync(ctx, a.client, resp.CollectorPack)
	}
	a.window.tick(time.Now())
	a.writeStatus(resp.Status, nil)
	if resp.Status != a.status {
		log.Printf("stato agente: %s", resp.Status)
		a.status = resp.Status
	}
	a.approved.Store(resp.Status == "approved")
	if a.approved.Load() {
		a.wasHealthy.Store(true)
		a.healthy.Do(func() { a.reportUpgradeOutcome(ctx) })
	}
}

// writeStatus aggiorna il file letto dall'icona nella tray (solo Windows).
func (a *Agent) writeStatus(state string, err error) {
	host, _ := os.Hostname()
	st := status.Status{
		Version: version.Version, State: state, Server: a.cfg.ServerURL, Device: host,
		RemoteExec: a.cfg.RemoteExecAllowed(), LastContact: a.lastOK, UpdatedAt: time.Now(),
	}
	if active, until := a.window.state(); active {
		st.BackupWindowUntil = &until
	} else if start, end, ok := a.window.upcoming(time.Now()); ok {
		st.BackupWindowStart, st.BackupWindowUntil = &start, &end
	}
	if err != nil {
		st.LastError = err.Error()
	}
	if werr := status.Write(status.Path(), st); werr != nil {
		log.Printf("file di stato: %v", werr)
	}
}

func (a *Agent) reportUpgradeOutcome(ctx context.Context) {
	out := upgrade.OnHealthy(a.cfg.DataDir, version.Version)
	if out == nil {
		return
	}
	sev := "info"
	if out.Type == "agent.upgrade_rollback" {
		sev = "warning"
	}
	log.Printf("esito aggiornamento: %s (%s → %s)", out.Type, out.From, out.To)
	a.sendEvent(ctx, client.Event{TS: time.Now().UTC(), Type: out.Type, Severity: sev,
		Data: map[string]any{"from": out.From, "to": out.To}})
}

func (a *Agent) sendEvent(ctx context.Context, ev client.Event) {
	env := a.client.NewEnvelope(map[string]any{"events": []client.Event{ev}})
	if err := a.client.Send(ctx, "/agent/v1/events", env); err != nil {
		log.Printf("invio evento %s: %v", ev.Type, err)
	}
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

// roles: ruoli dei collector integrati più quelli rilevati dal pacchetto di raccolta.
func (a *Agent) roles() []string {
	seen := map[string]bool{}
	var out []string
	for _, r := range append(Roles(a.collectors), a.packs.roles()...) {
		if !seen[r] {
			seen[r] = true
			out = append(out, r)
		}
	}
	return out
}

func (a *Agent) sendProbe(ctx context.Context, r probeResult) {
	if !a.approved.Load() {
		return
	}
	if !r.OK {
		log.Printf("probe %s: %s", r.Probe, r.Error)
	}
	if err := a.client.Send(ctx, "/agent/v1/probe", a.client.NewEnvelope(r)); err != nil {
		log.Printf("invio probe %s: %v", r.Probe, err)
	}
}

func (a *Agent) sendInventory(ctx context.Context) {
	env := a.client.NewEnvelope(CollectInventory(ctx, a.collectors))
	if err := a.client.Send(ctx, "/agent/v1/inventory", env); err != nil {
		log.Printf("invio inventario: %v", err)
	}
}

func (a *Agent) sendReports(ctx context.Context) {
	for _, c := range a.collectors {
		r, ok := c.(Reporter)
		if !ok {
			continue
		}
		if a.packs.replaces(c.Name()) {
			continue // la raccolta arriva dal pacchetto
		}
		path, payload, err := r.Report(ctx)
		if err != nil {
			log.Printf("report %s: %v", c.Name(), err)
			// In console diventa un problema, chiuso dal primo report riuscito.
			msg := err.Error()
			if len(msg) > 2000 {
				msg = msg[:2000]
			}
			a.sendEvent(ctx, client.Event{TS: time.Now().UTC(), Type: "collector.report_failed", Severity: "warning",
				Data: map[string]any{"collector": c.Name(), "error": msg}})
			continue
		}
		if path == "" {
			continue // niente da riportare (es. Hyper Backup non installato)
		}
		if err := a.client.Send(ctx, path, a.client.NewEnvelope(payload)); err != nil {
			log.Printf("invio report %s: %v", c.Name(), err)
		}
	}
}
