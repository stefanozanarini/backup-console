// Package synology raccoglie lo stato dei NAS Synology (DSM 7): modello, versione di DSM,
// dischi con SMART e temperatura (smartctl), RAID (md) e, a parte, i task di Hyper Backup.
package synology

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"backupconsole/agent/internal/collector"
)

const (
	synoinfo   = "/etc.defaults/synoinfo.conf"
	versionCfg = "/etc.defaults/VERSION"
)

type runner func(ctx context.Context, name string, args ...string) (string, error)

func execRunner(ctx context.Context, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).Output()
	return string(out), err
}

type Collector struct {
	run      runner
	readFile func(string) ([]byte, error)
	glob     func(string) ([]string, error)
}

func New() *Collector { return &Collector{run: execRunner, readFile: os.ReadFile, glob: filepath.Glob} }

func (*Collector) Name() string { return "synology" }
func (*Collector) Role() string { return "synology" }

func (c *Collector) Detect() bool {
	b, err := c.readFile(versionCfg)
	if err != nil {
		return false
	}
	_, err = c.readFile(synoinfo)
	return err == nil && strings.Contains(string(b), `os_name="DSM"`)
}

// kv legge i file chiave="valore" di DSM (VERSION, synoinfo.conf).
func kv(b []byte) map[string]string {
	out := map[string]string{}
	sc := bufio.NewScanner(strings.NewReader(string(b)))
	for sc.Scan() {
		k, v, ok := strings.Cut(strings.TrimSpace(sc.Text()), "=")
		if ok {
			out[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"`)
		}
	}
	return out
}

// dsmVersion: "DSM 7.2.2-72806 Update 9".
func dsmVersion(v map[string]string) string {
	s := strings.TrimSpace("DSM " + v["productversion"])
	if b := v["buildnumber"]; b != "" {
		s += "-" + b
	}
	if u := v["smallfixnumber"]; u != "" && u != "0" {
		s += " Update " + u
	}
	return s
}

func (c *Collector) Inventory(ctx context.Context) (collector.Inventory, error) {
	vb, _ := c.readFile(versionCfg)
	ib, _ := c.readFile(synoinfo)
	ver, info := kv(vb), kv(ib)
	model := info["upnpmodelname"]
	serial := ""
	if b, err := c.readFile("/proc/sys/kernel/syno_serial"); err == nil {
		serial = strings.TrimSpace(string(b))
	}
	disks := []map[string]any{}
	for _, d := range c.disks(ctx) {
		disks = append(disks, map[string]any{"slot": d.slot, "smart": d.smartRaw, "temp_c": d.tempC, "model": d.model, "serial": d.serial})
	}
	data := map[string]any{"firmware": dsmVersion(ver), "build": ver["buildnumber"], "disks": disks}
	if arrays := c.arrays(); len(arrays) > 0 {
		data["arrays"] = arrays
	}
	return collector.Inventory{Model: model, Serial: serial, OSVersion: dsmVersion(ver), Data: data}, nil
}

func (c *Collector) Metrics(ctx context.Context) ([]collector.Metric, error) {
	var out []collector.Metric
	for _, d := range c.disks(ctx) {
		l := map[string]string{"slot": d.slot}
		if d.tempC != nil {
			out = append(out, collector.NewMetric("hw.disk_temp_c", *d.tempC, l))
		}
		if d.smartOK != nil {
			out = append(out, collector.NewMetric("hw.disk_smart_ok", boolf(*d.smartOK), l))
		}
	}
	for _, a := range c.arrays() {
		out = append(out, collector.NewMetric("raid.healthy", boolf(a.Healthy), map[string]string{"array": a.Name}))
	}
	return out, nil
}

type disk struct {
	slot, model, serial, smartRaw string
	smartOK                       *bool
	tempC                         *float64
}

var (
	reSata = regexp.MustCompile(`^/dev/(sata\d+|sd[a-z]+|nvme\d+n\d+)$`)
	reNum  = regexp.MustCompile(`\d+`)
)

// devices: dischi interi (niente partizioni). DSM 7 usa /dev/sataN, i modelli più vecchi /dev/sdX.
func (c *Collector) devices() []string {
	var out []string
	seen := map[string]bool{}
	for _, pat := range []string{"/dev/sata*", "/dev/sd?", "/dev/nvme*n1"} {
		m, _ := c.glob(pat)
		for _, p := range m {
			if reSata.MatchString(p) && !seen[p] {
				seen[p] = true
				out = append(out, p)
			}
		}
	}
	// Se ci sono i nomi sataN, sdX sono gli stessi dischi (o USB esterni): meglio i primi.
	hasSata := false
	for _, p := range out {
		if strings.HasPrefix(p, "/dev/sata") {
			hasSata = true
		}
	}
	if hasSata {
		filtered := out[:0]
		for _, p := range out {
			if !strings.HasPrefix(p, "/dev/sd") {
				filtered = append(filtered, p)
			}
		}
		out = filtered
	}
	sort.Slice(out, func(i, j int) bool { return slotKey(out[i]) < slotKey(out[j]) })
	return out
}

func slotKey(p string) string {
	n := reNum.FindString(filepath.Base(p))
	if len(n) < 3 {
		n = strings.Repeat("0", 3-len(n)) + n
	}
	return strings.TrimRight(filepath.Base(p), "0123456789") + n
}

func (c *Collector) disks(ctx context.Context) []disk {
	var out []disk
	for _, dev := range c.devices() {
		// smartctl esce con un codice diverso da 0 anche quando risponde (bit di stato):
		// conta l'output. Su DSM i dischi sataN spesso danno salute e attributi solo con il
		// tipo esplicito: se mancano si riprova con -d sat e poi -d ata.
		var d disk
		found := false
		for _, typ := range []string{"", "sat", "ata"} {
			args := []string{"-H", "-i", "-A"}
			if typ != "" {
				args = append(args, "-d", typ)
			}
			txt, _ := c.run(ctx, "smartctl", append(args, dev)...)
			if strings.TrimSpace(txt) == "" {
				continue
			}
			cur := parseSmartctl(txt)
			if !found || (cur.smartOK != nil && d.smartOK == nil) || (cur.tempC != nil && d.tempC == nil) {
				if cur.model == "" {
					cur.model = d.model
				}
				if cur.serial == "" {
					cur.serial = d.serial
				}
				d, found = cur, true
			}
			if d.smartOK != nil && d.tempC != nil {
				break
			}
		}
		if !found {
			continue
		}
		name := filepath.Base(dev)
		d.slot = name
		if strings.HasPrefix(name, "sata") {
			d.slot = strings.TrimPrefix(name, "sata")
		}
		out = append(out, d)
	}
	return out
}

var (
	reModel  = regexp.MustCompile(`(?m)^(Device Model|Model Number|Product):\s*(.+)$`)
	reSerial = regexp.MustCompile(`(?m)^Serial Number:\s*(.+)$`)
	reHealth = regexp.MustCompile(`(?m)^(SMART overall-health self-assessment test result|SMART Health Status):\s*(.+)$`)
	// Attributo 194 (o 190): il primo numero del valore grezzo.
	reAttrTemp = regexp.MustCompile(`(?m)^\s*(194|190)\s+\S+\s+(?:\S+\s+){7}(\d+)`)
	reNvmeTemp = regexp.MustCompile(`(?m)^Temperature:\s*(\d+)\s*Celsius`)
)

func parseSmartctl(s string) disk {
	var d disk
	if m := reModel.FindStringSubmatch(s); m != nil {
		d.model = strings.TrimSpace(m[2])
	}
	if m := reSerial.FindStringSubmatch(s); m != nil {
		d.serial = strings.TrimSpace(m[1])
	}
	if m := reHealth.FindStringSubmatch(s); m != nil {
		raw := strings.TrimSpace(m[2])
		ok := strings.EqualFold(raw, "PASSED") || strings.EqualFold(raw, "OK")
		d.smartOK = &ok
		d.smartRaw = map[bool]string{true: "GOOD", false: raw}[ok]
	}
	var t string
	if m := reAttrTemp.FindStringSubmatch(s); m != nil {
		t = m[2]
	} else if m := reNvmeTemp.FindStringSubmatch(s); m != nil {
		t = m[1]
	}
	if v, err := strconv.ParseFloat(t, 64); err == nil && v > 0 && v < 100 {
		d.tempC = &v
	}
	return d
}

type Array struct {
	Name    string `json:"name"`
	Healthy bool   `json:"healthy"`
	Detail  string `json:"detail,omitempty"`
}

// md0 (sistema) e md1 (swap) di DSM occupano una partizione su tutti gli alloggiamenti:
// con il NAS non pieno risultano "degradati" per costruzione ([8/4] [UUUU____]).
var systemMD = map[string]bool{"md0": true, "md1": true}

var reMdStatus = regexp.MustCompile(`\[(\d+)/(\d+)\]\s+\[([U_]+)\]`)

func parseMdstat(s string) []Array {
	var out []Array
	var cur, level string
	for _, line := range strings.Split(s, "\n") {
		if name, rest, ok := strings.Cut(line, " : "); ok && strings.HasPrefix(name, "md") {
			cur = strings.TrimSpace(name)
			f := strings.Fields(rest)
			level = ""
			if len(f) > 1 {
				level = f[1]
			}
			continue
		}
		if cur == "" {
			continue
		}
		m := reMdStatus.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		if !systemMD[cur] {
			detail := strings.TrimSpace(fmt.Sprintf("%s %s", level, m[0]))
			out = append(out, Array{Name: cur, Healthy: !strings.Contains(m[3], "_") && m[1] == m[2], Detail: detail})
		}
		cur = ""
	}
	return out
}

func (c *Collector) arrays() []Array {
	b, err := c.readFile("/proc/mdstat")
	if err != nil {
		return nil
	}
	return parseMdstat(string(b))
}

func boolf(b bool) float64 {
	if b {
		return 1
	}
	return 0
}
