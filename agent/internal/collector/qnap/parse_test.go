package qnap

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestParseTemp(t *testing.T) {
	cases := map[string]float64{"43 C/109 F": 43, "38": 38, "41°C": 41}
	for in, want := range cases {
		if got, ok := parseTemp(in); !ok || got != want {
			t.Errorf("parseTemp(%q) = %v,%v; atteso %v", in, got, ok, want)
		}
	}
	for _, in := range []string{"", "--", "-- C/-- F", "n/a"} {
		if _, ok := parseTemp(in); ok {
			t.Errorf("parseTemp(%q) doveva fallire", in)
		}
	}
}

func TestParseSmart(t *testing.T) {
	for in, want := range map[string]bool{"GOOD": true, "Normal": true, "Abnormal": false, "Warning": false} {
		if ok, known := parseSmart(in); !known || ok != want {
			t.Errorf("parseSmart(%q) = %v,%v", in, ok, known)
		}
	}
	if _, known := parseSmart("--"); known {
		t.Error("'--' non deve essere riconosciuto")
	}
}

const mdstat = `Personalities : [linear] [raid0] [raid1] [raid10] [raid6] [raid5] [raid4]
md1 : active raid5 sda3[0] sdb3[1] sdc3[2]
      11691190848 blocks super 1.0 level 5, 64k chunk, algorithm 2 [3/3] [UUU]

md2 : active raid1 sdd3[0]
      3897063616 blocks super 1.0 [2/1] [U_]

md9 : active raid1 sda1[0] sdb1[1] sdc1[2]
      530112 blocks super 1.0 [24/3] [UUU_____________________]

unused devices: <none>
`

func TestParseMdstatSkipsSystemArrays(t *testing.T) {
	got := parseMdstat(mdstat)
	if len(got) != 2 {
		t.Fatalf("array = %+v", got)
	}
	if got[0].Name != "md1" || !got[0].Healthy {
		t.Errorf("md1: %+v", got[0])
	}
	if got[1].Name != "md2" || got[1].Healthy {
		t.Errorf("md2 doveva risultare degradato: %+v", got[1])
	}
}

func TestParseZpool(t *testing.T) {
	got := parseZpoolList("zpool1\tONLINE\nzpool2\tDEGRADED\n")
	if len(got) != 2 || !got[0].Healthy || got[1].Healthy {
		t.Fatalf("%+v", got)
	}
}

func TestMetricsWithFakeQNAP(t *testing.T) {
	outputs := map[string]string{
		"/sbin/getsysinfo systmp":    "45 C/113 F",
		"/sbin/getsysinfo cputmp":    "52 C/125 F",
		"/sbin/getsysinfo hdnum":     "3",
		"/sbin/getsysinfo hdsmart 1": "GOOD",
		"/sbin/getsysinfo hdtmp 1":   "36 C/96 F",
		"/sbin/getsysinfo hdsmart 2": "Abnormal",
		"/sbin/getsysinfo hdtmp 2":   "41 C/105 F",
		"/sbin/getsysinfo hdsmart 3": "--",
		"/sbin/getsysinfo hdtmp 3":   "--",
		"/sbin/getsysinfo sysfannum": "1",
		"/sbin/getsysinfo sysfan 1":  "1180 RPM",
	}
	c := &Collector{
		run: func(_ context.Context, name string, args ...string) (string, error) {
			if v, ok := outputs[strings.Join(append([]string{name}, args...), " ")]; ok {
				return v, nil
			}
			return "", errors.New("not found")
		},
		readFile: func(string) ([]byte, error) { return []byte(mdstat), nil },
		exists:   func(string) bool { return false },
	}
	ms, err := c.Metrics(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]float64{}
	for _, m := range ms {
		key := m.Name
		for _, k := range []string{"slot", "fan", "array"} {
			if v := m.Labels[k]; v != "" {
				key += ":" + v
			}
		}
		got[key] = m.Value
	}
	want := map[string]float64{
		"hw.sys_temp_c": 45, "hw.cpu_temp_c": 52,
		"hw.disk_temp_c:1": 36, "hw.disk_smart_ok:1": 1,
		"hw.disk_temp_c:2": 41, "hw.disk_smart_ok:2": 0,
		"hw.fan_rpm:1": 1180, "raid.healthy:md1": 1, "raid.healthy:md2": 0,
	}
	if len(got) != len(want) {
		t.Fatalf("metriche = %v", got)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %v, atteso %v", k, got[k], v)
		}
	}
}
