//go:build !windows

package veeam

import (
	"context"
	"errors"
)

func detectVBR() bool { return false }
func detectVAW() bool { return false }

func runPowerShell(context.Context, string) ([]byte, error) {
	return nil, errors.New("PowerShell disponibile solo su Windows")
}
