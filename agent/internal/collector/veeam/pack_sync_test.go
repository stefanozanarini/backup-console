package veeam

import (
	"os"
	"path/filepath"
	"testing"
)

// Durante il passaggio ai pacchetti di raccolta gli script integrati nell'agente restano
// come riserva (agenti senza pacchetto): devono restare identici a quelli di packs/.
func TestScriptsMatchCollectorPack(t *testing.T) {
	packDir := filepath.Join("..", "..", "..", "..", "packs", "scripts")
	if _, err := os.Stat(packDir); err != nil {
		t.Skip("cartella packs/ non disponibile")
	}
	for _, name := range []string{"common.ps1", "vbr.ps1", "vaw.ps1", "uranium.ps1"} {
		a, err1 := os.ReadFile(filepath.Join("scripts", name))
		b, err2 := os.ReadFile(filepath.Join(packDir, name))
		if err1 != nil || err2 != nil {
			t.Fatalf("%s: %v %v", name, err1, err2)
		}
		if string(a) != string(b) {
			t.Errorf("%s: diverso tra agent/internal/collector/veeam/scripts e packs/scripts", name)
		}
	}
}
