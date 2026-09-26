//go:build windows

package remote

import (
	"os/exec"
	"strconv"
)

// prepare: al timeout termina l'albero di processi con taskkill.
func prepare(cmd *exec.Cmd) {
	cmd.Cancel = func() error {
		return exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid)).Run()
	}
}
