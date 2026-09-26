//go:build linux

package upgrade

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeDL struct{ data []byte }

func (f fakeDL) Download(_ context.Context, _ string, w io.Writer, _ int64) (int64, error) {
	n, err := io.Copy(w, bytes.NewReader(f.data))
	return n, err
}

func script(version string) []byte {
	return []byte("#!/bin/sh\necho " + version + "\n")
}

type env struct {
	dir, exe string
	priv     ed25519.PrivateKey
}

func setup(t *testing.T) env {
	t.Helper()
	dir := t.TempDir()
	exe := filepath.Join(dir, "bc-agent")
	if err := os.WriteFile(exe, script("0.1.0"), 0o755); err != nil {
		t.Fatal(err)
	}
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	oldKey, oldExe := PublicKey, executable
	PublicKey = base64.StdEncoding.EncodeToString(pub)
	executable = func() (string, error) { return exe, nil }
	t.Cleanup(func() { PublicKey, executable = oldKey, oldExe })
	return env{dir: dir, exe: exe, priv: priv}
}

func (e env) request(data []byte, version string) Request {
	sum := sha256.Sum256(data)
	return Request{
		Version: version, Platform: Platform(), SHA256: hex.EncodeToString(sum[:]), Size: int64(len(data)),
		Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(e.priv, data)), Path: "/x",
	}
}

func content(t *testing.T, p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestApplyInstallsAndKeepsPrevious(t *testing.T) {
	e := setup(t)
	bin := script("0.2.0")
	if err := Apply(context.Background(), fakeDL{bin}, e.request(bin, "0.2.0"), Options{CurrentVersion: "0.1.0", DataDir: e.dir}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(content(t, e.exe), "0.2.0") || !strings.Contains(content(t, e.exe+".prev"), "0.1.0") {
		t.Fatal("swap non eseguito")
	}
	if _, ok := readMarker(e.dir); !ok {
		t.Fatal("marker mancante")
	}
	if _, err := os.Stat(e.exe + ".new"); !os.IsNotExist(err) {
		t.Fatal(".new non rimosso")
	}
}

func TestApplyRejectsBadSignatureAndHash(t *testing.T) {
	e := setup(t)
	bin := script("0.2.0")
	req := e.request(bin, "0.2.0")

	badSig := req
	badSig.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(e.priv, []byte("altro")))
	if err := Apply(context.Background(), fakeDL{bin}, badSig, Options{CurrentVersion: "0.1.0", DataDir: e.dir}); err == nil || !strings.Contains(err.Error(), "firma") {
		t.Fatalf("attesa firma non valida, ottenuto %v", err)
	}
	tampered := append([]byte{}, bin...)
	tampered[len(tampered)-2] = '9'
	if err := Apply(context.Background(), fakeDL{tampered}, req, Options{CurrentVersion: "0.1.0", DataDir: e.dir}); err == nil || !strings.Contains(err.Error(), "sha256") {
		t.Fatalf("atteso sha256 errato, ottenuto %v", err)
	}
	if !strings.Contains(content(t, e.exe), "0.1.0") {
		t.Fatal("l'eseguibile attuale non doveva cambiare")
	}
}

func TestApplyPreflightCatchesWrongVersion(t *testing.T) {
	e := setup(t)
	bin := script("0.3.0") // dichiarato 0.2.0 ma il binario dice 0.3.0
	err := Apply(context.Background(), fakeDL{bin}, e.request(bin, "0.2.0"), Options{CurrentVersion: "0.1.0", DataDir: e.dir})
	if err == nil || !strings.Contains(err.Error(), "riporta la versione") {
		t.Fatalf("atteso errore di preflight, ottenuto %v", err)
	}
	if !strings.Contains(content(t, e.exe), "0.1.0") {
		t.Fatal("l'eseguibile attuale non doveva cambiare")
	}
}

func TestApplyWithoutKeyIsRefused(t *testing.T) {
	e := setup(t)
	PublicKey = ""
	bin := script("0.2.0")
	if err := Apply(context.Background(), fakeDL{bin}, e.request(bin, "0.2.0"), Options{CurrentVersion: "0.1.0", DataDir: e.dir}); err == nil {
		t.Fatal("senza chiave l'aggiornamento va rifiutato")
	}
	if err := Apply(context.Background(), fakeDL{bin}, e.request(bin, "0.2.0"), Options{CurrentVersion: "0.1.0", DataDir: e.dir, AllowUnsigned: true}); err != nil {
		t.Fatalf("con allow_unsigned doveva passare: %v", err)
	}
}

func TestRollbackAfterRepeatedFailedStarts(t *testing.T) {
	e := setup(t)
	bin := script("0.2.0")
	if err := Apply(context.Background(), fakeDL{bin}, e.request(bin, "0.2.0"), Options{CurrentVersion: "0.1.0", DataDir: e.dir}); err != nil {
		t.Fatal(err)
	}
	logf := func(string, ...any) {}
	for i := 0; i < MaxAttempts; i++ {
		if err := OnStartup(e.dir, "0.2.0", logf); err != nil {
			t.Fatalf("avvio %d: %v", i+1, err)
		}
	}
	if err := OnStartup(e.dir, "0.2.0", logf); !errors.Is(err, ErrRestart) {
		t.Fatalf("atteso rollback, ottenuto %v", err)
	}
	if !strings.Contains(content(t, e.exe), "0.1.0") {
		t.Fatal("versione precedente non ripristinata")
	}
	// La vecchia versione riparte, arriva all'heartbeat e segnala il rollback.
	if err := OnStartup(e.dir, "0.1.0", logf); err != nil {
		t.Fatal(err)
	}
	out := OnHealthy(e.dir, "0.1.0")
	if out == nil || out.Type != "agent.upgrade_rollback" || out.To != "0.2.0" {
		t.Fatalf("esito = %+v", out)
	}
	if _, ok := readMarker(e.dir); ok {
		t.Fatal("marker non rimosso")
	}
}

func TestHealthyUpgradeReportsSuccess(t *testing.T) {
	e := setup(t)
	bin := script("0.2.0")
	_ = Apply(context.Background(), fakeDL{bin}, e.request(bin, "0.2.0"), Options{CurrentVersion: "0.1.0", DataDir: e.dir})
	_ = OnStartup(e.dir, "0.2.0", func(string, ...any) {})
	out := OnHealthy(e.dir, "0.2.0")
	if out == nil || out.Type != "agent.upgraded" {
		t.Fatalf("esito = %+v", out)
	}
}
