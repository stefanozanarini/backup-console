package agent

import (
	"strings"
	"testing"
)

func TestSyncQPKGVersion(t *testing.T) {
	var calls []string
	run := func(name string, args ...string) (string, error) {
		calls = append(calls, name+" "+strings.Join(args, " "))
		return "0.2.17\n", nil
	}
	nolog := func(string, ...any) {}

	syncQPKGVersion("0.3.1", run, nolog) // fuori da QNAP: niente
	if len(calls) != 0 {
		t.Fatalf("chiamate fuori da QNAP: %v", calls)
	}
	t.Setenv("QNAP_QPKG", "BackupConsoleAgent")
	syncQPKGVersion("0.3.1", run, nolog)
	if len(calls) != 2 || calls[1] != "/sbin/setcfg BackupConsoleAgent Version 0.3.1 -f /etc/config/qpkg.conf" {
		t.Fatalf("chiamate: %v", calls)
	}
	calls = nil
	syncQPKGVersion("0.2.17", run, nolog) // già allineata
	if len(calls) != 1 {
		t.Fatalf("setcfg inutile: %v", calls)
	}
}
