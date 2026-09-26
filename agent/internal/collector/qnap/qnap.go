// Package qnap raccoglie stato hardware e RAID dai NAS QNAP (QTS 4.x/5.x, QuTS hero).
//
// Le utility di sistema (getsysinfo, getcfg, get_hwsn) cambiano formato tra modelli e
// versioni: i parser sono tolleranti e ogni valore non interpretabile viene ignorato
// invece di bloccare la raccolta. Da validare sui modelli reali della flotta.
package qnap

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"backupconsole/agent/internal/collector"
)

const ulinuxConf = "/etc/config/uLinux.conf"

type runner func(ctx context.Context, name string, args ...string) (string, error)

func execRunner(ctx context.Context, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).Output()
	return strings.TrimSpace(string(out)), err
}

type Collector struct {
	run      runner
	readFile func(string) ([]byte, error)
	exists   func(string) bool
}

func New() *Collector {
	return &Collector{run: execRunner, readFile: os.ReadFile, exists: fileExists}
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func (*Collector) Name() string { return "qnap" }

func (c *Collector) Detect() bool {
	return c.exists(ulinuxConf) && c.exists("/sbin/getsysinfo")
}

// Role distingue QuTS hero (ZFS) da QTS.
func (c *Collector) Role() string {
	if c.zpoolPath() != "" {
		return "qnap_hero"
	}
	return "qnap"
}

func (c *Collector) zpoolPath() string {
	for _, p := range []string{"/sbin/zpool", "/usr/sbin/zpool", "/usr/local/sbin/zpool"} {
		if c.exists(p) {
			return p
		}
	}
	return ""
}

func (c *Collector) getcfg(section, field string) string {
	out, err := c.run(context.Background(), "/sbin/getcfg", section, field, "-f", ulinuxConf)
	if err != nil {
		return ""
	}
	return out
}

func (c *Collector) sysinfo(ctx context.Context, args ...string) string {
	out, err := c.run(ctx, "/sbin/getsysinfo", args...)
	if err != nil {
		return ""
	}
	return out
}

func (c *Collector) Inventory(ctx context.Context) (collector.Inventory, error) {
	model := c.sysinfo(ctx, "model")
	if model == "" {
		model = c.getcfg("System", "Model")
	}
	fw := c.getcfg("System", "Version")
	build := c.getcfg("System", "Build Number")
	serial, _ := c.run(ctx, "/sbin/get_hwsn")

	osName := "QTS"
	if c.Role() == "qnap_hero" {
		osName = "QuTS hero"
	}
	osVersion := strings.TrimSpace(fmt.Sprintf("%s %s %s", osName, fw, build))

	disks := []map[string]any{}
	for _, d := range c.disks(ctx) {
		disks = append(disks, map[string]any{
			"slot": d.slot, "smart": d.smartRaw, "temp_c": d.tempC, "model": d.model,
		})
	}
	data := map[string]any{"firmware": fw, "build": build, "disks": disks}
	if arrays := c.arrays(ctx); len(arrays) > 0 {
		data["arrays"] = arrays
	}
	return collector.Inventory{Model: model, Serial: serial, OSVersion: osVersion, Data: data}, nil
}

func (c *Collector) Metrics(ctx context.Context) ([]collector.Metric, error) {
	var out []collector.Metric
	if t, ok := parseTemp(c.sysinfo(ctx, "systmp")); ok {
		out = append(out, collector.NewMetric("hw.sys_temp_c", t, nil))
	}
	if t, ok := parseTemp(c.sysinfo(ctx, "cputmp")); ok {
		out = append(out, collector.NewMetric("hw.cpu_temp_c", t, nil))
	}
	for _, d := range c.disks(ctx) {
		l := map[string]string{"slot": d.slot}
		if d.tempC != nil {
			out = append(out, collector.NewMetric("hw.disk_temp_c", *d.tempC, l))
		}
		if d.smartOK != nil {
			out = append(out, collector.NewMetric("hw.disk_smart_ok", boolf(*d.smartOK), l))
		}
	}
	if n, err := strconv.Atoi(c.sysinfo(ctx, "sysfannum")); err == nil {
		for i := 1; i <= n; i++ {
			if rpm, ok := parseRPM(c.sysinfo(ctx, "sysfan", strconv.Itoa(i))); ok {
				out = append(out, collector.NewMetric("hw.fan_rpm", rpm, map[string]string{"fan": strconv.Itoa(i)}))
			}
		}
	}
	for _, a := range c.arrays(ctx) {
		out = append(out, collector.NewMetric("raid.healthy", boolf(a.Healthy), map[string]string{"array": a.Name}))
	}
	return out, nil
}

type disk struct {
	slot     string
	model    string
	smartRaw string
	smartOK  *bool
	tempC    *float64
}

func (c *Collector) disks(ctx context.Context) []disk {
	n, err := strconv.Atoi(c.sysinfo(ctx, "hdnum"))
	if err != nil {
		return nil
	}
	var out []disk
	for i := 1; i <= n; i++ {
		slot := strconv.Itoa(i)
		smart := c.sysinfo(ctx, "hdsmart", slot)
		temp, tempOK := parseTemp(c.sysinfo(ctx, "hdtmp", slot))
		if !tempOK && isEmptySlot(smart) {
			continue // alloggiamento vuoto
		}
		d := disk{slot: slot, smartRaw: smart, model: c.sysinfo(ctx, "hdmodel", slot)}
		if tempOK {
			d.tempC = &temp
		}
		if ok, known := parseSmart(smart); known {
			d.smartOK = &ok
		}
		out = append(out, d)
	}
	return out
}

type Array struct {
	Name    string `json:"name"`
	Healthy bool   `json:"healthy"`
	Detail  string `json:"detail,omitempty"`
}

func (c *Collector) arrays(ctx context.Context) []Array {
	var out []Array
	if b, err := c.readFile("/proc/mdstat"); err == nil {
		out = append(out, parseMdstat(string(b))...)
	}
	if zp := c.zpoolPath(); zp != "" {
		if s, err := c.run(ctx, zp, "list", "-H", "-o", "name,health"); err == nil {
			out = append(out, parseZpoolList(s)...)
		}
	}
	return out
}

func boolf(b bool) float64 {
	if b {
		return 1
	}
	return 0
}
