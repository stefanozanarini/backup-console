//go:build windows

package veeam

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"os/exec"
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
func runPowerShell(ctx context.Context, script string) ([]byte, error) {
	enc := encodePS(script)
	var lastErr error
	for _, exe := range []string{"powershell.exe", "pwsh.exe"} {
		if _, err := exec.LookPath(exe); err != nil {
			continue
		}
		cmd := exec.CommandContext(ctx, exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc)
		var stdout, stderr bytes.Buffer
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
