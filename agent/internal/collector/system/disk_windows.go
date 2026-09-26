package system

import (
	"os/exec"
	"strings"
	"syscall"
	"unsafe"
)

var (
	kernel32              = syscall.NewLazyDLL("kernel32.dll")
	procGetLogicalDrives  = kernel32.NewProc("GetLogicalDriveStringsW")
	procGetDriveType      = kernel32.NewProc("GetDriveTypeW")
	procGetDiskFreeSpaceE = kernel32.NewProc("GetDiskFreeSpaceExW")
)

const driveFixed = 3

func volumes() ([]Volume, error) {
	buf := make([]uint16, 256)
	n, _, err := procGetLogicalDrives.Call(uintptr(len(buf)), uintptr(unsafe.Pointer(&buf[0])))
	if n == 0 {
		return nil, err
	}
	var out []Volume
	// Il buffer contiene "C:\<NUL>D:\<NUL>...<NUL>".
	for i := 0; i < int(n); {
		j := i
		for j < int(n) && buf[j] != 0 {
			j++
		}
		if j == i {
			break
		}
		root := syscall.UTF16ToString(buf[i:j])
		i = j + 1

		p, _ := syscall.UTF16PtrFromString(root)
		if t, _, _ := procGetDriveType.Call(uintptr(unsafe.Pointer(p))); t != driveFixed {
			continue
		}
		var avail, total, free uint64
		ok, _, _ := procGetDiskFreeSpaceE.Call(
			uintptr(unsafe.Pointer(p)),
			uintptr(unsafe.Pointer(&avail)),
			uintptr(unsafe.Pointer(&total)),
			uintptr(unsafe.Pointer(&free)),
		)
		if ok == 0 || total == 0 {
			continue
		}
		out = append(out, Volume{
			Mount: root, TotalBytes: total, FreeBytes: avail, UsedPct: usedPct(total, free, avail),
		})
	}
	return out, nil
}

func osVersion() string {
	out, err := exec.Command("cmd", "/c", "ver").Output()
	if err != nil {
		return "windows"
	}
	return strings.TrimSpace(string(out))
}
