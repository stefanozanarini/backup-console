//go:build !windows

package remote

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"
)

type collect struct {
	mu     sync.Mutex
	chunks []string
}

func (c *collect) sink(s string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.chunks = append(c.chunks, s)
}

func (c *collect) all() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return strings.Join(c.chunks, "")
}

func TestRunCapturesOutputAndExitCode(t *testing.T) {
	var c collect
	code, err := Run(context.Background(), "sh", "echo uno; echo due >&2; printf 'città'; exit 3", 10*time.Second, c.sink)
	if err != nil || code != 3 {
		t.Fatalf("code=%d err=%v", code, err)
	}
	if got := c.all(); got != "uno\ndue\ncittà" {
		t.Fatalf("output %q", got)
	}
}

func TestRunStreamsWhileRunning(t *testing.T) {
	var c collect
	start := time.Now()
	go func() {
		_, _ = Run(context.Background(), "sh", "echo inizio; sleep 3; echo fine", 10*time.Second, c.sink)
	}()
	for time.Since(start) < 2500*time.Millisecond {
		if strings.Contains(c.all(), "inizio") {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("l'output non è arrivato prima della fine del comando")
}

func TestRunTimeoutKillsChildren(t *testing.T) {
	var c collect
	start := time.Now()
	_, err := Run(context.Background(), "sh", "sleep 30 & sleep 30; echo mai", time.Second, c.sink)
	if !errors.Is(err, ErrTimeout) {
		t.Fatalf("atteso timeout, ottenuto %v", err)
	}
	if time.Since(start) > 5*time.Second {
		t.Fatal("il timeout non ha terminato i processi figli")
	}
}

func TestSafeCutKeepsIncompleteRune(t *testing.T) {
	b := []byte("ab\xc3") // prima metà di "à"
	if safeCut(b) != 2 {
		t.Fatalf("safeCut = %d", safeCut(b))
	}
	if safeCut([]byte("abà")) != 4 {
		t.Fatal("stringa completa non deve essere tagliata")
	}
}
