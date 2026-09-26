//go:build !linux && !windows

package system

import "runtime"

func volumes() ([]Volume, error) { return nil, nil }

func osVersion() string { return runtime.GOOS }
