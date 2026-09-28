//go:build windows && embedtray

// Package trayembed contiene l'eseguibile dell'icona (bc-agent-tray.exe, compilato con
// -H windowsgui) dentro bc-agent.exe: un solo file da rilasciare e l'icona si aggiorna
// insieme all'agente. La CI lo compila in bin/ prima di bc-agent con -tags embedtray.
package trayembed

import _ "embed"

//go:embed bin/bc-agent-tray.exe
var Binary []byte
