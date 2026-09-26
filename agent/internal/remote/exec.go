// Package remote esegue i comandi richiesti dalla console.
package remote

import (
	"context"
	"errors"
	"io"
	"os/exec"
	"runtime"
	"sync"
	"time"
	"unicode/utf8"
)

// Sink riceve l'output a blocchi (stdout e stderr mescolati, in ordine di arrivo).
type Sink func(chunk string)

const (
	flushEvery = time.Second
	flushBytes = 32 << 10
)

var ErrTimeout = errors.New("timeout")

// ShellCommand costruisce la riga di comando per la shell richiesta.
func ShellCommand(ctx context.Context, shell, command string) *exec.Cmd {
	if shell == "" || shell == "default" {
		shell = "sh"
		if runtime.GOOS == "windows" {
			shell = "powershell"
		}
	}
	// Output UTF-8 anche con Windows PowerShell 5.1 (altrimenti usa la codepage OEM).
	const psUTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
	switch shell {
	case "bash":
		return exec.CommandContext(ctx, "bash", "-c", command)
	case "powershell":
		return exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", psUTF8+command)
	case "pwsh":
		return exec.CommandContext(ctx, "pwsh", "-NoProfile", "-NonInteractive", "-Command", psUTF8+command)
	case "cmd":
		return exec.CommandContext(ctx, "cmd.exe", "/C", command)
	default:
		return exec.CommandContext(ctx, "/bin/sh", "-c", command)
	}
}

// Run esegue il comando e ritorna il codice di uscita. Con il timeout termina
// l'intero gruppo di processi e ritorna ErrTimeout.
func Run(ctx context.Context, shell, command string, timeout time.Duration, sink Sink) (int, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := ShellCommand(ctx, shell, command)
	prepare(cmd)
	pr, pw := io.Pipe()
	cmd.Stdout, cmd.Stderr = pw, pw

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		pump(pr, sink)
	}()

	err := cmd.Start()
	if err == nil {
		err = cmd.Wait()
	}
	_ = pw.Close()
	wg.Wait()

	if ctx.Err() == context.DeadlineExceeded {
		return -1, ErrTimeout
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return exitErr.ExitCode(), nil
	}
	if err != nil {
		return -1, err
	}
	return 0, nil
}

// pump legge l'output e lo passa al sink ogni secondo o ogni 32 KB, senza spezzare
// caratteri UTF-8 a metà.
func pump(r io.Reader, sink Sink) {
	var (
		mu  sync.Mutex
		buf []byte
	)
	flush := func(final bool) {
		mu.Lock()
		cut := len(buf)
		if !final {
			cut = safeCut(buf)
		}
		chunk := buf[:cut]
		buf = append([]byte{}, buf[cut:]...)
		mu.Unlock()
		if len(chunk) > 0 {
			sink(string(chunk))
		}
	}

	done := make(chan struct{})
	go func() {
		t := time.NewTicker(flushEvery)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-t.C:
				flush(false)
			}
		}
	}()

	tmp := make([]byte, 8192)
	for {
		n, err := r.Read(tmp)
		if n > 0 {
			mu.Lock()
			buf = append(buf, tmp[:n]...)
			big := len(buf) >= flushBytes
			mu.Unlock()
			if big {
				flush(false)
			}
		}
		if err != nil {
			break
		}
	}
	close(done)
	flush(true)
}

// safeCut ritorna la lunghezza massima di b che non termina con un carattere UTF-8 incompleto.
func safeCut(b []byte) int {
	for i := len(b) - 1; i >= 0 && i >= len(b)-utf8.UTFMax; i-- {
		if utf8.RuneStart(b[i]) {
			if !utf8.FullRune(b[i:]) {
				return i
			}
			break
		}
	}
	return len(b)
}
