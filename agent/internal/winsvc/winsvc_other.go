//go:build !windows

package winsvc

import (
	"context"
	"errors"
)

const Name = "BackupConsoleAgent"

var errUnsupported = errors.New("servizio Windows non disponibile su questa piattaforma")

func IsService() bool                                   { return false }
func Run(func(ctx context.Context) error, uint32) error { return errUnsupported }
func Install(string, ...string) error                   { return errUnsupported }
func Start() error                                      { return errUnsupported }
func Stop() error                                       { return errUnsupported }
func Remove() error                                     { return errUnsupported }
