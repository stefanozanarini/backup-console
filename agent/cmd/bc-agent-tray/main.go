// bc-agent-tray: icona della Backup Console nell'area di notifica di Windows.
// Compilato con -ldflags "-H windowsgui" (nessuna finestra di console) e incluso in
// bc-agent.exe, che lo installa e lo avvia a ogni accesso degli utenti.
package main

import (
	"os"

	"backupconsole/agent/internal/tray"
)

func main() {
	if err := tray.Run(); err != nil {
		os.Exit(1)
	}
}
