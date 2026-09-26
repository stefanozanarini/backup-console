package system

import (
	"bufio"
	"io"
	"os"
	"strings"
	"syscall"
)

// Filesystem "veri". Esclude tmpfs, proc, overlay, ecc.
var realFS = map[string]bool{
	"ext2": true, "ext3": true, "ext4": true, "xfs": true, "btrfs": true,
	"zfs": true, "f2fs": true, "vfat": true, "exfat": true, "ntfs": true, "ntfs3": true, "ufsd": true,
}

type mount struct{ device, point, fstype string }

// parseMounts legge il formato di /proc/mounts, tenendo un solo mount point per device
// e scartando i device RAM/loop (su QNAP la root è un ramdisk).
func parseMounts(r io.Reader) []mount {
	seen := map[string]bool{}
	var out []mount
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 3 || !realFS[f[2]] {
			continue
		}
		dev, point := f[0], unescapeMount(f[1])
		if strings.HasPrefix(dev, "/dev/ram") || strings.HasPrefix(dev, "/dev/loop") || seen[dev] {
			continue
		}
		seen[dev] = true
		out = append(out, mount{device: dev, point: point, fstype: f[2]})
	}
	return out
}

// /proc/mounts codifica gli spazi come \040.
func unescapeMount(s string) string {
	return strings.NewReplacer(`\040`, " ", `\011`, "\t", `\134`, `\`).Replace(s)
}

func volumes() ([]Volume, error) {
	f, err := os.Open("/proc/mounts")
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []Volume
	for _, m := range parseMounts(f) {
		var st syscall.Statfs_t
		if err := syscall.Statfs(m.point, &st); err != nil {
			continue
		}
		bs := uint64(st.Bsize)
		total, free, avail := uint64(st.Blocks)*bs, uint64(st.Bfree)*bs, uint64(st.Bavail)*bs
		if total == 0 {
			continue
		}
		out = append(out, Volume{
			Mount: m.point, Device: m.device, FSType: m.fstype,
			TotalBytes: total, FreeBytes: avail, UsedPct: usedPct(total, free, avail),
		})
	}
	return out, nil
}

func osVersion() string {
	b, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return "linux"
	}
	for _, line := range strings.Split(string(b), "\n") {
		if v, ok := strings.CutPrefix(line, "PRETTY_NAME="); ok {
			return strings.Trim(v, `"`)
		}
	}
	return "linux"
}
