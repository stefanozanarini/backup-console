package system

import "testing"

func TestUsedPctMatchesDf(t *testing.T) {
	// 100 blocchi, 20 liberi di cui 15 disponibili ai non-root: df mostra 80/(80+15) = 84%.
	got := usedPct(100, 20, 15)
	if got < 84.2 || got > 84.3 {
		t.Fatalf("usedPct = %.2f, atteso ~84.21", got)
	}
	if usedPct(0, 0, 0) != 0 {
		t.Fatal("volume vuoto deve dare 0")
	}
}

func TestCleanID(t *testing.T) {
	for in, want := range map[string]string{"431202296": "431202296", " 431 202 296 ": "431202296", "12": "", "": ""} {
		if got := cleanID(in); got != want {
			t.Errorf("cleanID(%q) = %q, atteso %q", in, got, want)
		}
	}
}
