package veeam

import (
	"context"
	"testing"
)

// Output tipico dello script VBR (PowerShell 5.1 con BOM e un avviso prima del JSON).
const sample = "\xef\xbb\xbfAVVISO: qualcosa\r\n" + `{"product":"vbr","version":"12.3.1.1139","jobs":[` +
	`{"id":"6a1f","name":"Backup VM","type":"Backup","enabled":true,"last_result":"Success","last_state":"Stopped",` +
	`"last_start":"2026-09-25T22:00:03.0000000Z","last_end":"2026-09-25T22:41:10.0000000Z","transferred_bytes":1234567,` +
	`"next_run":null,"details":{"description":""}},` +
	`{"id":"b2","name":"Copia NAS","type":"BackupCopy","enabled":true,"last_result":"failed","last_state":"Stopped"},` +
	`{"id":"c3","name":"Mai partito","enabled":false,"last_result":null}],` +
	`"repositories":[{"name":"Repo QNAP","type":"LinuxLocal","total_bytes":8000000000000,"free_bytes":1200000000000}],` +
	`"license":{"edition":"EnterprisePlus","status":"Valid","expiration":"2027-03-31T00:00:00.0000000Z"},` +
	`"errors":["Get-VBRBackupCopyJob: comando non trovato"]}`

func TestParseNormalizesResults(t *testing.T) {
	p, err := Parse([]byte(sample))
	if err != nil {
		t.Fatal(err)
	}
	got := []string{p.Jobs[0].LastResult, p.Jobs[1].LastResult, p.Jobs[2].LastResult}
	want := []string{"Success", "Failed", "None"}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("job %d: %q, atteso %q", i, got[i], want[i])
		}
	}
	if p.Jobs[0].LastEnd == nil || p.Jobs[0].LastEnd.Hour() != 22 {
		t.Errorf("last_end non interpretato: %v", p.Jobs[0].LastEnd)
	}
	if p.License == nil || p.License.Expiration.Year() != 2027 {
		t.Errorf("licenza: %+v", p.License)
	}
	if len(p.Errors) != 1 || *p.Repositories[0].FreeBytes != 1200000000000 {
		t.Errorf("payload: %+v", p)
	}
}

func TestReportUsesScriptOutput(t *testing.T) {
	var script string
	c := &Collector{product: "vbr", role: "veeam_vbr", script: vbrPS, detect: func() bool { return true },
		run: func(_ context.Context, s string) ([]byte, error) { script = s; return []byte(sample), nil }}
	path, payload, err := c.Report(context.Background())
	if err != nil || path != "/agent/v1/veeam" {
		t.Fatalf("path=%s err=%v", path, err)
	}
	if payload.(Payload).Product != "vbr" || len(payload.(Payload).Jobs) != 3 {
		t.Fatalf("payload %+v", payload)
	}
	if len(script) < len(vbrPS) || script[:len(commonPS)] != commonPS {
		t.Fatal("lo script eseguito deve includere le funzioni comuni")
	}
	inv, _ := c.Inventory(context.Background())
	if inv.Data["version"] != "12.3.1.1139" {
		t.Fatalf("inventario %+v", inv.Data)
	}
}

func TestParseRejectsGarbage(t *testing.T) {
	if _, err := Parse([]byte("Import-Module : impossibile caricare")); err == nil {
		t.Fatal("atteso errore")
	}
}
