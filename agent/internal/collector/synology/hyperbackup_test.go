package synology

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// Dati reali di un RS1221+ (task "BackupNASAzureBlob" verso Azure Blob).
var hbFiles = map[string]string{
	hbConf: "[global]\n[repo_1]\nname=\"\"\naccess_key=\"SEGRETA\"\n[task_1]\nname=\"BackupNASAzureBlob\"\npassword=\"SEGRETA\"\n",
	hbBackupLast: `[global]
version="4.1.2-4039"
[task_1]
data=null
end_time=1790710746
error=""
error_code=0
last_backup_success_time=1790709979
last_backup_success_version=397
processed_size=5492140258890
result="done"
start_time=1790710746
target_size=7593855507
total_size=9214680726330
`,
	hbDetectLast: "[task_1]\nend_time=1790550896\nerror=\"\"\nerror_code=0\nresult=\"done\"\nstart_time=0\n",
	hbLog: `2026-09-28T21:32:10+02:00 ALANASSEDE01 img_backup[1]: (1) [err] backup_progress.cpp:504 Backup task [BackupNASAzureBlob] completes with result [1]. Time spent: [2500 sec].
2026-09-29T21:26:18+02:00 ALANASSEDE01 img_backup[23763]: sysnotify_send_notification.cpp:479 SYSNOTIFY: [BackupTaskFinished] was sent to desktop
2026-09-29T21:26:19+02:00 ALANASSEDE01 img_backup[23763]: (23763) [err] backup_progress.cpp:504 Backup task [BackupNASAzureBlob] completes with result [1]. Time spent: [2177 sec].
`,
	hbLog + ".1.xz": "x",
}

func hbCollector() *Collector {
	return &Collector{
		readFile: func(p string) ([]byte, error) {
			if s, ok := hbFiles[p]; ok {
				return []byte(s), nil
			}
			return nil, errors.New("no")
		},
		run: func(_ context.Context, name string, args ...string) (string, error) {
			if name == "xz" {
				return "2026-09-27T21:31:00+02:00 NAS img_backup[1]: Backup task [BackupNASAzureBlob] completes with result [4]. Time spent: [100 sec].\n", nil
			}
			return "", errors.New("no")
		},
		glob: func(string) ([]string, error) { return nil, nil },
	}
}

func TestHyperBackup(t *testing.T) {
	c := hbCollector()
	path, payload, err := c.Report(context.Background())
	if err != nil || path != "/agent/v1/hyperbackup" {
		t.Fatalf("%s %v", path, err)
	}
	if payload == nil {
		t.Fatal("payload vuoto")
	}
	pl := c.hyperBackup(context.Background())
	if len(pl.Jobs) != 1 {
		t.Fatalf("job: %+v", pl.Jobs)
	}
	j := pl.Jobs[0]
	if j.ID != "hyperbackup:task_1" || j.Name != "BackupNASAzureBlob" || j.LastResult != "Success" {
		t.Fatalf("%+v", j)
	}
	if !j.LastEnd.Equal(time.Date(2026, 9, 29, 19, 26, 19, 0, time.UTC)) || j.LastStart.Sub(*j.LastEnd) != -2177*time.Second {
		t.Fatalf("tempi: %v %v", j.LastStart, j.LastEnd)
	}
	if j.LastSuccess.Unix() != 1790709979 || *j.TransferredBytes != 7593855507 {
		t.Fatalf("successo/dati: %v %v", j.LastSuccess, *j.TransferredBytes)
	}
	h := j.Details["history"].([]map[string]any)
	if len(h) != 3 || h[2]["result"] != "Warning" || !strings.Contains(h[2]["reason"].(string), "codice 4") {
		t.Fatalf("storico: %+v", h)
	}
	if j.Details["integrity_check"].(map[string]any)["result"] != "Success" {
		t.Fatal("verifica di integrità")
	}
	// Le credenziali della configurazione non devono comparire da nessuna parte.
	if out, _ := jsonString(pl); strings.Contains(out, "SEGRETA") {
		t.Fatal("credenziali nel payload")
	}
}

func TestLastResult(t *testing.T) {
	for in, want := range map[string]string{"done": "Success", "fail": "Failed", "partial_done": "Warning", "cancel": "Warning", "backingup": "None"} {
		if got, _ := lastResult(map[string]string{"result": in}); got != want {
			t.Errorf("%s: %s, atteso %s", in, got, want)
		}
	}
	if got, msg := lastResult(map[string]string{"result": "fail", "error": "Destinazione non raggiungibile"}); got != "Failed" || msg != "Destinazione non raggiungibile" {
		t.Fatal(got, msg)
	}
}

func jsonString(v any) (string, error) {
	b, err := json.Marshal(v)
	return string(b), err
}
