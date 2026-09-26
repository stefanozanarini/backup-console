//go:build !windows

package main

import "errors"

var errWindowsOnly = errors.New("install/uninstall sono disponibili solo su Windows (su QNAP usare il QPKG)")

func install([]string) error   { return errWindowsOnly }
func uninstall([]string) error { return errWindowsOnly }
