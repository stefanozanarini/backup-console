//go:build !windows

package remote

import (
	"os/exec"
	"syscall"
)

// prepare mette il comando in un gruppo di processi proprio, così il timeout termina
// anche i figli (es. rsync lanciato da uno script).
func prepare(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}
