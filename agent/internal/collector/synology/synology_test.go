package synology

import (
	"context"
	"errors"
	"testing"
)

// /proc/mdstat di un RS1221+ con 4 dischi (dall'uscita reale).
const mdstat = `Personalities : [raid1] [raid6] [raid5] [raid4] [raidF1]
md2 : active raid5 sata1p3[0] sata2p3[2] sata3p3[1]
      23416322048 blocks super 1.2 level 5, 64k chunk, algorithm 2 [3/3] [UUU]

md1 : active raid1 sata1p2[0] sata4p2[3] sata2p2[2] sata3p2[1]
      2097088 blocks [8/4] [UUUU____]

md0 : active raid1 sata1p1[0] sata4p1[3] sata2p1[2] sata3p1[1]
      8388544 blocks [8/4] [UUUU____]

unused devices: <none>
`

func TestMdstatSkipsSystemArrays(t *testing.T) {
	a := parseMdstat(mdstat)
	if len(a) != 1 || a[0].Name != "md2" || !a[0].Healthy || a[0].Detail != "raid5 [3/3] [UUU]" {
		t.Fatalf("%+v", a)
	}
	deg := parseMdstat("md2 : active raid5 sata1p3[0] sata2p3[2]\n      123 blocks [3/2] [UU_]\n")
	if len(deg) != 1 || deg[0].Healthy {
		t.Fatalf("degradato non rilevato: %+v", deg)
	}
}

const smartSata = `smartctl 6.5 (build date Jun 18 2026) [x86_64-linux-4.4.302+] (local build)
=== START OF INFORMATION SECTION ===
Device Model:     WDC WD80EFZZ-68BTXN0
Serial Number:    WD-CA0ABCDE
=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  9 Power_On_Hours          0x0032   088   088   000    Old_age   Always       -       9123
194 Temperature_Celsius     0x0022   119   106   000    Old_age   Always       -       33 (Min/Max 19/46)
`

func TestParseSmartctl(t *testing.T) {
	d := parseSmartctl(smartSata)
	if d.model != "WDC WD80EFZZ-68BTXN0" || d.serial != "WD-CA0ABCDE" || d.smartRaw != "GOOD" || !*d.smartOK || *d.tempC != 33 {
		t.Fatalf("%+v temp=%v", d, d.tempC)
	}
	bad := parseSmartctl("SMART overall-health self-assessment test result: FAILED!\n")
	if *bad.smartOK || bad.smartRaw != "FAILED!" {
		t.Fatalf("%+v", bad)
	}
	nvme := parseSmartctl("Model Number: Synology SNV3410\nSMART overall-health self-assessment test result: PASSED\nTemperature: 41 Celsius\n")
	if *nvme.tempC != 41 {
		t.Fatal("temperatura NVMe")
	}
}

func TestInventory(t *testing.T) {
	files := map[string]string{
		versionCfg:                     "productversion=\"7.2.2\"\nbuildnumber=\"72806\"\nsmallfixnumber=\"9\"\nos_name=\"DSM\"\n",
		synoinfo:                       "unique=\"synology_v1000_rs1221+\"\nupnpmodelname=\"RS1221+\"\n",
		"/proc/mdstat":                 mdstat,
		"/proc/sys/kernel/syno_serial": "2150ABCDEF\n",
	}
	c := &Collector{
		readFile: func(p string) ([]byte, error) {
			if s, ok := files[p]; ok {
				return []byte(s), nil
			}
			return nil, errors.New("no")
		},
		glob: func(p string) ([]string, error) {
			if p == "/dev/sata*" {
				return []string{"/dev/sata2", "/dev/sata1", "/dev/sata1p1"}, nil
			}
			if p == "/dev/sd?" {
				return []string{"/dev/sda"}, nil
			}
			return nil, nil
		},
		run: func(context.Context, string, ...string) (string, error) { return smartSata, nil },
	}
	if !c.Detect() {
		t.Fatal("DSM non rilevato")
	}
	inv, err := c.Inventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if inv.Model != "RS1221+" || inv.Serial != "2150ABCDEF" || inv.OSVersion != "DSM 7.2.2-72806 Update 9" {
		t.Fatalf("%+v", inv)
	}
	disks := inv.Data["disks"].([]map[string]any)
	if len(disks) != 2 || disks[0]["slot"] != "1" || disks[1]["slot"] != "2" {
		t.Fatalf("dischi: %+v", disks)
	}
}

func TestSmartRetriesWithDeviceType(t *testing.T) {
	c := &Collector{
		readFile: func(string) ([]byte, error) { return nil, errors.New("no") },
		glob: func(p string) ([]string, error) {
			if p == "/dev/sata*" {
				return []string{"/dev/sata1"}, nil
			}
			return nil, nil
		},
		// Senza -d risponde solo con le informazioni del disco (com'è su DSM); con -d sat tutto.
		run: func(_ context.Context, _ string, args ...string) (string, error) {
			for _, a := range args {
				if a == "sat" {
					return smartSata, errors.New("exit status 4")
				}
			}
			return "Device Model:     WDC WD122KRYZ-01CDAB0\nSMART support is: Unavailable\n", errors.New("exit status 4")
		},
	}
	d := c.disks(context.Background())
	if len(d) != 1 || d[0].smartOK == nil || !*d[0].smartOK || d[0].tempC == nil || *d[0].tempC != 33 {
		t.Fatalf("%+v", d)
	}
}
