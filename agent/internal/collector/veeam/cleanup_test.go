package veeam

import (
	"context"
	"strings"
	"testing"
)

func fakeVBR(out string, script *string) *Collector {
	return &Collector{product: "vbr", role: "veeam_vbr", detect: func() bool { return true },
		run: func(_ context.Context, s string) ([]byte, error) { *script = s; return []byte(out), nil }}
}

const scanOut = `{"older_than_days":30,"items":[` +
	`{"id":"0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f","name":"Vecchio job","state":"orphan","size_bytes":1073741824,"candidate":true},` +
	`{"id":"11111111-2222-3333-4444-555555555555","name":"Backup VM","state":"active","size_bytes":5,"candidate":false}],` +
	`"repositories":[{"name":"NAS","total_bytes":100,"free_bytes":3}],"errors":[]}`

func TestCleanupScanBuildsScriptAndParses(t *testing.T) {
	var script string
	c := fakeVBR(scanOut, &script)
	res, err := c.CleanupScan(context.Background(), 30)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(script, "$OlderThanDays = 30\n") || !strings.Contains(script, "function Get-BackupInfo") {
		t.Fatal("script senza soglia o senza funzioni comuni")
	}
	if strings.Contains(script, "Remove-VBRBackup") {
		t.Fatal("l'analisi non deve contenere comandi di cancellazione")
	}
	if items := res["items"].([]any); len(items) != 2 {
		t.Fatalf("items = %v", items)
	}
}

func TestCleanupScanRejectsLowThreshold(t *testing.T) {
	var script string
	if _, err := fakeVBR(scanOut, &script).CleanupScan(context.Background(), 3); err == nil {
		t.Fatal("soglia sotto il minimo accettata")
	}
	if script != "" {
		t.Fatal("lo script non doveva partire")
	}
}

func TestCleanupDeleteQuotesOnlyGUIDs(t *testing.T) {
	var script string
	c := fakeVBR(`{"results":[],"repositories":[],"errors":[]}`, &script)
	id := "0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f"
	if _, err := c.CleanupDelete(context.Background(), 30, []string{id}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(script, "$BackupIds = @('"+id+"')") || !strings.Contains(script, "Remove-VBRBackup") {
		t.Fatal("id non passati allo script")
	}
	for _, bad := range []string{"x'); Remove-Item C:\\ -Recurse; ('", "", "0b5c1f3e"} {
		script = ""
		if _, err := c.CleanupDelete(context.Background(), 30, []string{bad}); err == nil || script != "" {
			t.Fatalf("id %q accettato", bad)
		}
	}
}

func TestStartJobValidation(t *testing.T) {
	var got string
	vbr := &Collector{product: "vbr", run: func(_ context.Context, s string) ([]byte, error) {
		got = s
		return []byte(`{"started":true}`), nil
	}}
	if _, err := vbr.StartJob(context.Background(), "x'; Remove-Item C:\\ -Recurse; '", "", "start"); err == nil {
		t.Fatal("id non GUID accettato")
	}
	if _, err := vbr.StartJob(context.Background(), "0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f", "", "boom"); err == nil {
		t.Fatal("modalità non valida accettata")
	}
	if _, err := vbr.StartJob(context.Background(), "0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f", "", "retry"); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "$JobId = '0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f'") || !strings.Contains(got, "$Mode = 'retry'") {
		t.Fatal("parametri non passati allo script")
	}
	vaw := &Collector{product: "vaw", run: vbr.run}
	if _, err := vaw.StartJob(context.Background(), "vaw:Backup-OPT", "Backup'OPT", "start"); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(got, "$JobName = 'BackupOPT'") {
		t.Fatal("apice non rimosso dal nome")
	}
}
