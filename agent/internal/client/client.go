// Package client implementa il protocollo v1 agente → console (vedi console/backend/app/schemas/agent.py).
package client

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync/atomic"
	"time"
)

type Client struct {
	base string
	hc   *http.Client
	seq  atomic.Int64
}

// New crea il client. cert è nil solo per l'enrollment (prima di avere un certificato).
func New(serverURL string, cert *tls.Certificate, serverCAFile string) (*Client, error) {
	tlsCfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if cert != nil {
		tlsCfg.Certificates = []tls.Certificate{*cert}
	}
	if serverCAFile != "" {
		pem, err := os.ReadFile(serverCAFile)
		if err != nil {
			return nil, err
		}
		pool, err := x509.SystemCertPool()
		if err != nil || pool == nil {
			pool = x509.NewCertPool()
		}
		if !pool.AppendCertsFromPEM(pem) {
			return nil, fmt.Errorf("nessun certificato valido in %s", serverCAFile)
		}
		tlsCfg.RootCAs = pool
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = tlsCfg
	c := &Client{
		base: strings.TrimRight(serverURL, "/"),
		hc:   &http.Client{Transport: transport, Timeout: 60 * time.Second},
	}
	c.seq.Store(time.Now().UnixMilli())
	return c, nil
}

type HTTPError struct {
	Status int
	Body   string
}

func (e *HTTPError) Error() string { return fmt.Sprintf("HTTP %d: %s", e.Status, e.Body) }

func (c *Client) Post(ctx context.Context, path string, body, out any) error {
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		return &HTTPError{Status: resp.StatusCode, Body: strings.TrimSpace(string(data))}
	}
	if out != nil {
		return json.Unmarshal(data, out)
	}
	return nil
}

type Envelope struct {
	MsgID   string    `json:"msg_id"`
	Seq     int64     `json:"seq"`
	TS      time.Time `json:"ts"`
	Payload any       `json:"payload"`
}

type IngestResponse struct {
	Accepted  bool `json:"accepted"`
	Duplicate bool `json:"duplicate"`
}

// NewEnvelope assegna msg_id e seq: lo stesso envelope reinviato (es. dal buffer) viene
// riconosciuto come duplicato dalla console.
func (c *Client) NewEnvelope(payload any) Envelope {
	return Envelope{MsgID: uuid4(), Seq: c.seq.Add(1), TS: time.Now().UTC(), Payload: payload}
}

func (c *Client) Send(ctx context.Context, path string, env Envelope) error {
	var r IngestResponse
	return c.Post(ctx, path, env, &r)
}

func uuid4() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

// --- messaggi -----------------------------------------------------------

type EnrollRequest struct {
	Token        string   `json:"token"`
	CSRPEM       string   `json:"csr_pem"`
	Hostname     string   `json:"hostname"`
	Platform     string   `json:"platform"`
	Arch         string   `json:"arch"`
	AgentVersion string   `json:"agent_version"`
	Roles        []string `json:"roles"`
	AllowShell   bool     `json:"allow_shell"`
}

type EnrollResponse struct {
	AgentID  string `json:"agent_id"`
	DeviceID string `json:"device_id"`
	Status   string `json:"status"`
	CertPEM  string `json:"cert_pem"`
	CAPEM    string `json:"ca_pem"`
}

type HeartbeatRequest struct {
	Version      string         `json:"version"`
	Roles        []string       `json:"roles"`
	Capabilities map[string]any `json:"capabilities"`
	AllowShell   bool           `json:"allow_shell"`
	UptimeS      int64          `json:"uptime_s"`
}

type HeartbeatResponse struct {
	Status         string    `json:"status"`
	ServerTime     time.Time `json:"server_time"`
	ConfigVersion  int       `json:"config_version"`
	DesiredVersion *string   `json:"desired_version"`
}
