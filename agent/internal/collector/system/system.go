// Package system raccoglie dati comuni a tutte le piattaforme: spazio disco e identità dell'host.
package system

import (
	"context"
	"os"
	"runtime"

	"backupconsole/agent/internal/collector"
)

type Collector struct{}

func New() *Collector { return &Collector{} }

func (*Collector) Name() string { return "system" }
func (*Collector) Role() string { return "system" }
func (*Collector) Detect() bool { return true }

type Volume struct {
	Mount      string  `json:"mount"`
	Device     string  `json:"device,omitempty"`
	FSType     string  `json:"fs_type,omitempty"`
	TotalBytes uint64  `json:"total_bytes"`
	FreeBytes  uint64  `json:"free_bytes"`
	UsedPct    float64 `json:"used_pct"`
}

// usedPct segue la convenzione di df: used / (used + disponibile per utenti non root).
func usedPct(total, free, avail uint64) float64 {
	if total == 0 || free > total {
		return 0
	}
	used := total - free
	if used+avail == 0 {
		return 0
	}
	return float64(used) * 100 / float64(used+avail)
}

func (c *Collector) Inventory(ctx context.Context) (collector.Inventory, error) {
	host, _ := os.Hostname()
	vols, err := volumes()
	return collector.Inventory{
		Hostname:  host,
		OSVersion: osVersion(),
		Data: map[string]any{
			"os":      runtime.GOOS,
			"arch":    runtime.GOARCH,
			"volumes": vols,
		},
	}, err
}

func (c *Collector) Metrics(ctx context.Context) ([]collector.Metric, error) {
	vols, err := volumes()
	out := make([]collector.Metric, 0, len(vols)*3)
	for _, v := range vols {
		l := map[string]string{"mount": v.Mount}
		out = append(out,
			collector.NewMetric("disk.used_pct", v.UsedPct, l),
			collector.NewMetric("disk.total_bytes", float64(v.TotalBytes), l),
			collector.NewMetric("disk.free_bytes", float64(v.FreeBytes), l),
		)
	}
	return out, err
}
