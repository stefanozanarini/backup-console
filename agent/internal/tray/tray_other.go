//go:build !windows

package tray

import "errors"

func Run() error { return errors.New("l'icona nella tray è disponibile solo su Windows") }
