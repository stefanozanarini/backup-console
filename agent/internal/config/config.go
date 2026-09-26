// Package config gestisce il file di configurazione locale dell'agente.
//
// Alcune impostazioni (allow_shell) si decidono solo in installazione e la
// console non può modificarle: è il consenso locale del dispositivo.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

type Config struct {
	ServerURL string `json:"server_url"`
	AgentID   string `json:"agent_id,omitempty"`
	// Directory con agent.key, agent.crt, ca.crt. Default: la directory del file di config.
	DataDir string `json:"data_dir,omitempty"`
	// CA extra per verificare il certificato TLS del server (sviluppo o proxy con ispezione TLS).
	ServerCAFile string `json:"server_ca_file,omitempty"`
	AllowShell   bool   `json:"allow_shell"`

	HeartbeatSec int `json:"heartbeat_sec,omitempty"`
	MetricsSec   int `json:"metrics_sec,omitempty"`
	InventorySec int `json:"inventory_sec,omitempty"`

	path string
}

// DefaultPath dipende dalla piattaforma; il QPKG passa sempre -config esplicito.
func DefaultPath() string {
	if runtime.GOOS == "windows" {
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "BackupConsoleAgent", "agent.json")
	}
	return "/etc/bc-agent/agent.json"
}

func Load(path string) (*Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, fmt.Errorf("configurazione %s assente: eseguire prima 'bc-agent enroll'", path)
		}
		return nil, err
	}
	c := &Config{}
	if err := json.Unmarshal(b, c); err != nil {
		return nil, fmt.Errorf("configurazione %s non valida: %w", path, err)
	}
	c.path = path
	c.applyDefaults()
	return c, nil
}

func New(path, serverURL string) *Config {
	c := &Config{ServerURL: serverURL, path: path}
	c.applyDefaults()
	return c
}

func (c *Config) applyDefaults() {
	if c.DataDir == "" {
		c.DataDir = filepath.Dir(c.path)
	}
	if c.HeartbeatSec <= 0 {
		c.HeartbeatSec = 60
	}
	if c.MetricsSec <= 0 {
		c.MetricsSec = 300
	}
	if c.InventorySec <= 0 {
		c.InventorySec = 3600
	}
}

func (c *Config) Save() error {
	if err := os.MkdirAll(filepath.Dir(c.path), 0o700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	tmp := c.path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, c.path)
}
