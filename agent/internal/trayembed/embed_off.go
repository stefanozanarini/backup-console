//go:build !(windows && embedtray)

package trayembed

// Binary è vuoto nelle build senza icona (Linux, build locali senza -tags embedtray).
var Binary []byte
