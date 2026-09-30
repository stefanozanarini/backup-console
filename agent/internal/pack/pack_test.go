package pack

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"
)

func signed(t *testing.T, b Bundle) ([]byte, string, ed25519.PublicKey) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	raw, _ := json.Marshal(b)
	return raw, base64.StdEncoding.EncodeToString(ed25519.Sign(priv, raw)), pub
}

var sample = Bundle{Format: 1, Version: "1.0.0",
	Probes: []Probe{{ID: "veeam_vbr", Platform: "windows", Interpreter: "powershell", Scripts: []string{"common.ps1", "vbr.ps1"},
		Detect: &Cond{Service: "VeeamBackupSvc"}, Role: "veeam_vbr", Replaces: "veeam_vbr", IntervalS: 900, TimeoutS: 240}},
	Files: map[string]string{"common.ps1": "$a = 1", "vbr.ps1": "Write-Output $a"}}

func TestSignatureRequired(t *testing.T) {
	raw, sig, pub := signed(t, sample)
	b, err := Parse(raw, sig, pub)
	if err != nil || b.Version != "1.0.0" {
		t.Fatal(err)
	}
	if b.Script(b.Probes[0], "$IncludeHistory = $false") != "$IncludeHistory = $false\n$a = 1\nWrite-Output $a\n" {
		t.Fatal("script composto male")
	}
	// Un solo byte cambiato (es. console compromessa che altera lo script): rifiutato.
	tampered := append([]byte{}, raw...)
	tampered[len(tampered)-3] ^= 1
	if _, err := Parse(tampered, sig, pub); err == nil {
		t.Fatal("pacchetto alterato accettato")
	}
	other, _, _ := ed25519.GenerateKey(rand.Reader)
	if _, err := Parse(raw, sig, other); err == nil {
		t.Fatal("firma di un'altra chiave accettata")
	}
	if _, err := Parse(raw, sig, nil); err == nil {
		t.Fatal("agente senza chiave: i pacchetti non devono girare")
	}
}

func TestContentChecks(t *testing.T) {
	bad := sample
	bad.Probes = []Probe{{ID: "x", Interpreter: "bash", Scripts: []string{"common.ps1"}}}
	raw, sig, pub := signed(t, bad)
	if _, err := Parse(raw, sig, pub); err == nil {
		t.Fatal("interprete non ammesso accettato")
	}
	bad.Probes = []Probe{{ID: "x", Interpreter: "sh", Scripts: []string{"manca.sh"}}}
	raw, sig, pub = signed(t, bad)
	if _, err := Parse(raw, sig, pub); err == nil {
		t.Fatal("file mancante accettato")
	}
	bad = sample
	bad.Format = 2
	raw, sig, pub = signed(t, bad)
	if _, err := Parse(raw, sig, pub); err == nil {
		t.Fatal("formato futuro accettato")
	}
}

func TestCacheRoundTrip(t *testing.T) {
	raw, sig, pub := signed(t, sample)
	dir := t.TempDir()
	if err := Save(dir, raw, sig); err != nil {
		t.Fatal(err)
	}
	b, _, _, err := Load(dir, pub)
	if err != nil || b.Version != "1.0.0" {
		t.Fatal(err)
	}
}

type fi struct{ dir bool }

func (f fi) Name() string       { return "" }
func (f fi) Size() int64        { return 0 }
func (f fi) Mode() os.FileMode  { return 0 }
func (f fi) ModTime() time.Time { return time.Time{} }
func (f fi) IsDir() bool        { return f.dir }
func (f fi) Sys() any           { return nil }

func TestCond(t *testing.T) {
	e := Env{
		ServiceInstalled: func(n string) bool { return n == "VeeamEndpointBackupSvc" },
		Stat: func(p string) (os.FileInfo, error) {
			switch p {
			case `C:\ProgramData\UraniumBackup\Log`:
				return fi{dir: true}, nil
			case "/etc.defaults/VERSION":
				return fi{}, nil
			}
			return nil, errors.New("no")
		},
		ReadFile: func(p string) ([]byte, error) {
			if p == "/etc.defaults/VERSION" {
				return []byte(`os_name="DSM"`), nil
			}
			return nil, errors.New("no")
		},
		Getenv: func(k string) string { return map[string]string{"ProgramData": `C:\ProgramData`}[k] },
	}
	vaw := Cond{All: []Cond{{Service: "VeeamEndpointBackupSvc"}, {Not: &Cond{Service: "VeeamBackupSvc"}}}}
	uranium := Cond{Any: []Cond{{Dir: `%ProgramData%\UraniumBackup\Log`}, {File: `%ProgramFiles(x86)%\Uranium Backup\Uranium.exe`}}}
	dsm := Cond{FileContains: &FileContains{Path: "/etc.defaults/VERSION", Text: `os_name="DSM"`}}
	for name, c := range map[string]Cond{"vaw": vaw, "uranium": uranium, "dsm": dsm} {
		if !c.Eval(e) {
			t.Errorf("%s: atteso vero", name)
		}
	}
	if (&Cond{Service: "VeeamBackupSvc"}).Eval(e) || (&Cond{Dir: "/etc.defaults/VERSION"}).Eval(e) {
		t.Fatal("condizioni false risultate vere")
	}
}
