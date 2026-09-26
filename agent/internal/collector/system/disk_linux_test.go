package system

import (
	"strings"
	"testing"
)

// Estratto semplificato di /proc/mounts di un QNAP QTS 5.
const qnapMounts = `none / tmpfs rw,relatime,size=409600k,mode=755 0 0
/dev/ram0 / ext2 rw,relatime 0 0
proc /proc proc rw,relatime 0 0
/dev/md9 /mnt/HDA_ROOT ext3 rw,relatime,data=ordered 0 0
/dev/mapper/cachedev1 /share/CACHEDEV1_DATA ext4 rw,usrjquota=aquota.user 0 0
/dev/mapper/cachedev1 /share/CACHEDEV1_DATA/.qpkg ext4 rw 0 0
tmpfs /share ramfs rw 0 0
/dev/sdt1 /share/external/DEV3301_1 ufsd rw 0 0
/dev/sda1 /share/My\040Disk vfat rw 0 0
`

func TestParseMountsQNAP(t *testing.T) {
	got := parseMounts(strings.NewReader(qnapMounts))
	var points []string
	for _, m := range got {
		points = append(points, m.point)
	}
	want := "/mnt/HDA_ROOT,/share/CACHEDEV1_DATA,/share/external/DEV3301_1,/share/My Disk"
	if strings.Join(points, ",") != want {
		t.Fatalf("mount = %v\natteso %s", points, want)
	}
}
