package agent

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"backupconsole/agent/internal/pack"
)

func testPacks(run func(ctx context.Context, interpreter, script string) ([]byte, []byte, error)) *packs {
	return &packs{
		running: map[string]bool{}, lastRun: map[string]time.Time{}, maxOut: 1 << 20, run: run,
		env: pack.Env{
			ServiceInstalled: func(n string) bool { return n == "VeeamBackupSvc" },
			Stat:             func(string) (os.FileInfo, error) { return nil, errors.New("no") },
			ReadFile:         func(string) ([]byte, error) { return nil, errors.New("no") },
			Getenv:           func(string) string { return "" },
		},
		bundle: &pack.Bundle{Format: 1, Version: "1.2.0", Files: map[string]string{"a.ps1": "A"},
			Probes: []pack.Probe{
				{ID: "veeam_vbr", Interpreter: "powershell", Scripts: []string{"a.ps1"}, Detect: &pack.Cond{Service: "VeeamBackupSvc"},
					Role: "veeam_vbr", Replaces: "veeam_vbr", IntervalS: 900, TimeoutS: 5,
					Retry: &pack.Retry{Prelude: "$IncludeHistory = $false", Note: "storico sessioni non raccolto"}},
				{ID: "veeam_vaw", Interpreter: "powershell", Scripts: []string{"a.ps1"}, Detect: &pack.Cond{Service: "VeeamEndpointBackupSvc"},
					Role: "veeam_agent", Replaces: "veeam_vaw"},
			}},
	}
}

func TestPacksApplyAndReplace(t *testing.T) {
	p := testPacks(nil)
	p.bundle.Probes[0].Platform, p.bundle.Probes[1].Platform = "", ""
	if r := p.roles(); len(r) != 1 || r[0] != "veeam_vbr" {
		t.Fatalf("ruoli %v", r)
	}
	if !p.replaces("veeam_vbr") || p.replaces("veeam_vaw") {
		t.Fatal("sostituzione dei collector integrati")
	}
	now := time.Now()
	if d := p.due(now); len(d) != 1 || d[0].ID != "veeam_vbr" {
		t.Fatalf("probe da eseguire %v", d)
	}
	if len(p.due(now.Add(time.Minute))) != 0 {
		t.Fatal("in corso: non deve ripartire")
	}
	p.running["veeam_vbr"] = false
	if len(p.due(now.Add(10*time.Minute))) != 0 || len(p.due(now.Add(16*time.Minute))) != 1 {
		t.Fatal("intervallo non rispettato")
	}
}

func TestProbeRetryWithPrelude(t *testing.T) {
	var scripts []string
	p := testPacks(func(_ context.Context, _ string, s string) ([]byte, []byte, error) {
		scripts = append(scripts, s)
		if !strings.HasPrefix(s, "$IncludeHistory = $false") {
			return nil, []byte("boom"), errors.New("script troppo lungo")
		}
		return []byte(`{"product":"vbr"}`), nil, nil
	})
	r := p.execute(context.Background(), p.bundle.Probes[0])
	if !r.OK || r.Stdout != `{"product":"vbr"}` || len(scripts) != 2 || r.PackVersion != "1.2.0" {
		t.Fatalf("%+v", r)
	}
	if !strings.HasPrefix(r.Note, "storico sessioni non raccolto: script troppo lungo") {
		t.Fatalf("nota: %q", r.Note)
	}
	p.maxOut = 4
	p.run = func(context.Context, string, string) ([]byte, []byte, error) { return []byte("0123456789"), nil, nil }
	if r := p.execute(context.Background(), p.bundle.Probes[1]); r.Stdout != "0123" || !r.Truncated {
		t.Fatalf("output non troncato: %+v", r)
	}
}
