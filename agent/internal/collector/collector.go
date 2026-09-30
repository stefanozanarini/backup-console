// Package collector definisce l'interfaccia comune dei collector (sistema, QNAP, Veeam...).
package collector

import (
	"context"
	"time"
)

type Metric struct {
	TS     time.Time         `json:"ts"`
	Name   string            `json:"name"`
	Labels map[string]string `json:"labels,omitempty"`
	Value  float64           `json:"value"`
}

// Inventory: i campi noti aggiornano la scheda del dispositivo, Data finisce nello snapshot.
type Inventory struct {
	Hostname  string         `json:"hostname,omitempty"`
	Model     string         `json:"model,omitempty"`
	Serial    string         `json:"serial,omitempty"`
	OSVersion string         `json:"os_version,omitempty"`
	Data      map[string]any `json:"data"`
}

type Collector interface {
	Name() string
	// Role riportato alla console quando Detect è vero (es. "qnap", "veeam_vbr").
	Role() string
	Detect() bool
	Inventory(ctx context.Context) (Inventory, error)
	Metrics(ctx context.Context) ([]Metric, error)
}

func NewMetric(name string, value float64, labels map[string]string) Metric {
	return Metric{TS: time.Now().UTC(), Name: name, Labels: labels, Value: value}
}

// Merge unisce gli inventari: i campi noti del primo collector che li valorizza vincono,
// Data viene raccolta per nome del collector.
func Merge(parts map[string]Inventory) Inventory {
	out := Inventory{Data: map[string]any{}}
	for _, name := range []string{"qnap", "synology", "veeam_vbr", "veeam_vaw", "uranium", "system"} {
		p, ok := parts[name]
		if !ok {
			continue
		}
		if out.Hostname == "" {
			out.Hostname = p.Hostname
		}
		if out.Model == "" {
			out.Model = p.Model
		}
		if out.Serial == "" {
			out.Serial = p.Serial
		}
		if out.OSVersion == "" {
			out.OSVersion = p.OSVersion
		}
		out.Data[name] = p.Data
	}
	return out
}
