//go:build windows

package veeam

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"unicode/utf16"

	"golang.org/x/sys/windows/registry"
)

func serviceInstalled(name string) bool {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, `SYSTEM\CurrentControlSet\Services\`+name, registry.QUERY_VALUE)
	if err != nil {
		return false
	}
	_ = k.Close()
	return true
}

func detectVBR() bool { return serviceInstalled("VeeamBackupSvc") }

// Veeam Agent standalone: il servizio esiste anche quando l'agente è gestito da VBR;
// su un server VBR lo ignoriamo (i suoi job arrivano già dal collector VBR).
func detectVAW() bool { return serviceInstalled("VeeamEndpointBackupSvc") && !detectVBR() }

// Uranium Backup: la cartella dei log in ProgramData (c'è anche senza il servizio, che
// nella versione Free non esiste) o l'eseguibile installato.
func detectUranium() bool {
	if fi, err := os.Stat(filepath.Join(os.Getenv("ProgramData"), "UraniumBackup", "Log")); err == nil && fi.IsDir() {
		return true
	}
	for _, env := range []string{"ProgramFiles(x86)", "ProgramFiles"} {
		if base := os.Getenv(env); base != "" {
			if _, err := os.Stat(filepath.Join(base, "Uranium Backup", "Uranium.exe")); err == nil {
				return true
			}
		}
	}
	return false
}

func encodePS(script string) string {
	u := utf16.Encode([]rune(script))
	b := make([]byte, len(u)*2)
	for i, v := range u {
		b[2*i], b[2*i+1] = byte(v), byte(v>>8)
	}
	return base64.StdEncoding.EncodeToString(b)
}

// runPowerShell prova Windows PowerShell 5.1 e, se il modulo Veeam non si carica
// (Veeam B&R 13 lo richiede su PowerShell 7), ripiega su pwsh.
//
// Lo script non va sulla riga di comando: Windows la limita a 32767 caratteri e con
// -EncodedCommand (UTF-16 in base64) uno script di 12 KB la supera già. Sulla riga di
// comando c'è solo psBootstrap, lo script arriva da stdin (base64 dell'UTF-8, così
// non dipende dalla codifica della console) e non passa mai da un file su disco.
func runPowerShell(ctx context.Context, script string) ([]byte, error) {
	boot := encodePS(psBootstrap)
	input := base64.StdEncoding.EncodeToString([]byte(script))
	var lastErr error
	for _, exe := range []string{"powershell.exe", "pwsh.exe"} {
		if _, err := exec.LookPath(exe); err != nil {
			continue
		}
		cmd := exec.CommandContext(ctx, exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", boot)
		var stdout, stderr bytes.Buffer
		cmd.Stdin = strings.NewReader(input)
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		if err := cmd.Run(); err != nil {
			lastErr = fmt.Errorf("%s: %v: %s", exe, err, strings.TrimSpace(stderr.String()))
			continue
		}
		return stdout.Bytes(), nil
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("PowerShell non trovato")
	}
	return nil, lastErr
}
