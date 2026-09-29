//go:build !windows

package power

// KeepAwake non fa nulla fuori da Windows (la finestra di backup riguarda solo i PC).
func KeepAwake(bool) {}
