package synology

import (
	"bufio"
	"context"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"backupconsole/agent/internal/collector/veeam"
)

// Hyper Backup. Si leggono solo:
//   - i nomi dei task (righe name= di etc/synobackup.conf: lo stesso file contiene le
//     credenziali delle destinazioni, che non vanno lette);
//   - var/last_result/backup.last e detect.last (ultimo backup e ultima verifica di integrità);
//   - var/log/hyperbackup.log (e i precedenti .xz): "Backup task [nome] completes with
//     result [N]. Time spent: [S sec]." per lo storico.
const (
	hbRoot       = "/var/packages/HyperBackup"
	hbConf       = hbRoot + "/etc/synobackup.conf"
	hbBackupLast = hbRoot + "/var/last_result/backup.last"
	hbDetectLast = hbRoot + "/var/last_result/detect.last"
	hbLog        = hbRoot + "/var/log/hyperbackup.log"
)

// ini legge le sezioni [task_N] con chiave=valore; con only non vuoto tiene solo quelle chiavi.
func ini(s string, only ...string) map[string]map[string]string {
	keep := map[string]bool{}
	for _, k := range only {
		keep[k] = true
	}
	out := map[string]map[string]string{}
	var sec string
	sc := bufio.NewScanner(strings.NewReader(s))
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			sec = line[1 : len(line)-1]
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok || sec == "" || (len(keep) > 0 && !keep[strings.TrimSpace(k)]) {
			continue
		}
		if out[sec] == nil {
			out[sec] = map[string]string{}
		}
		out[sec][strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"`)
	}
	return out
}

type hbRun struct {
	task   string
	end    time.Time
	spent  time.Duration
	code   int
	result string
	reason string
}

var reHBDone = regexp.MustCompile(`^(\S+)\s.*Backup task \[(.+?)\] completes with result \[(\d+)\]\.(?:\s*Time spent:\s*\[(\d+)\s*sec\])?`)

// Codice nel log: 1 = riuscito (verificato con un task "Esito positivo"). Gli altri non
// sono documentati: li riportiamo come avvisi con il codice, l'esito ufficiale dell'ultima
// esecuzione arriva comunque da backup.last.
func codeResult(code int) (string, string) {
	if code == 1 {
		return "Success", "Backup completato"
	}
	return "Warning", "Backup terminato con codice " + strconv.Itoa(code)
}

func parseHBLog(s string) []hbRun {
	var out []hbRun
	sc := bufio.NewScanner(strings.NewReader(s))
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		m := reHBDone.FindStringSubmatch(sc.Text())
		if m == nil {
			continue
		}
		t, err := time.Parse(time.RFC3339, m[1])
		if err != nil {
			continue
		}
		code, _ := strconv.Atoi(m[3])
		r := hbRun{task: m[2], end: t, code: code}
		if m[4] != "" {
			if sec, err := strconv.Atoi(m[4]); err == nil {
				r.spent = time.Duration(sec) * time.Second
			}
		}
		r.result, r.reason = codeResult(code)
		out = append(out, r)
	}
	return out
}

// Esito di backup.last: "done", "fail", "partial", "cancel"… (e error/error_code).
func lastResult(v map[string]string) (string, string) {
	res := strings.ToLower(v["result"])
	msg := strings.TrimSpace(v["error"])
	switch {
	case res == "done" || res == "success" || res == "succeeded":
		if code := v["error_code"]; code != "" && code != "0" && msg != "" {
			return "Warning", msg
		}
		return "Success", ""
	case strings.Contains(res, "partial") || strings.Contains(res, "warn"):
		return "Warning", firstNonEmpty(msg, "backup completato parzialmente")
	case strings.Contains(res, "cancel") || strings.Contains(res, "abort") || strings.Contains(res, "suspend"):
		return "Warning", firstNonEmpty(msg, "backup annullato")
	case strings.Contains(res, "backing") || strings.Contains(res, "running") || strings.Contains(res, "waiting"):
		return "None", ""
	case res == "":
		return "None", msg
	}
	return "Failed", firstNonEmpty(msg, "backup non riuscito ("+res+")")
}

func firstNonEmpty(a ...string) string {
	for _, s := range a {
		if s != "" {
			return s
		}
	}
	return ""
}

func epoch(v string) *time.Time {
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n <= 0 {
		return nil
	}
	t := time.Unix(n, 0).UTC()
	return &t
}

func int64p(v string) *int64 {
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n < 0 {
		return nil
	}
	return &n
}

// hyperBackupLogs: log corrente e fino a 4 precedenti compressi (xz, se c'è il comando).
func (c *Collector) hyperBackupLogs(ctx context.Context) string {
	var b strings.Builder
	for i := 4; i >= 1; i-- {
		p := hbLog + "." + strconv.Itoa(i) + ".xz"
		if _, err := c.readFile(p); err != nil {
			continue
		}
		if out, err := c.run(ctx, "xz", "-dc", p); err == nil {
			b.WriteString(out)
			b.WriteByte('\n')
		}
	}
	if cur, err := c.readFile(hbLog); err == nil {
		b.Write(cur)
	}
	return b.String()
}

func (c *Collector) hasHyperBackup() bool {
	_, err := c.readFile(hbConf)
	return err == nil
}

// hyperBackup costruisce il payload per la console (stesso formato dei job Veeam).
func (c *Collector) hyperBackup(ctx context.Context) veeam.Payload {
	p := veeam.Payload{Product: "hyperbackup", Jobs: []veeam.Job{}, Repositories: []veeam.Repository{}, Errors: []string{}}
	if b, err := c.readFile(hbRoot + "/INFO"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if k, v, ok := strings.Cut(line, "="); ok && strings.TrimSpace(k) == "version" {
				p.Version = strings.Trim(strings.TrimSpace(v), `"`)
			}
		}
	}
	confB, err := c.readFile(hbConf)
	if err != nil {
		p.Errors = append(p.Errors, "configurazione di Hyper Backup non leggibile")
		return p
	}
	tasks := ini(string(confB), "name")
	lastB, _ := c.readFile(hbBackupLast)
	last := ini(string(lastB))
	detB, _ := c.readFile(hbDetectLast)
	detect := ini(string(detB))

	byName := map[string][]hbRun{}
	for _, r := range parseHBLog(c.hyperBackupLogs(ctx)) {
		byName[r.task] = append(byName[r.task], r)
	}

	ids := make([]string, 0, len(tasks))
	for id := range tasks {
		if strings.HasPrefix(id, "task_") {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	for _, id := range ids {
		name := tasks[id]["name"]
		if name == "" {
			name = id
		}
		runs := byName[name]
		sort.Slice(runs, func(i, j int) bool { return runs[i].end.After(runs[j].end) })
		lv := last[id]
		details := map[string]any{"task_id": id}

		j := veeam.Job{ID: "hyperbackup:" + id, Name: name, Type: "HyperBackup", Enabled: true, LastState: "Stopped"}
		result, reason := lastResult(lv)
		j.LastResult = result
		if reason != "" {
			details["messages"] = []map[string]any{{"object": nil, "status": map[bool]string{true: "error", false: "warning"}[result == "Failed"], "text": reason}}
		}
		// Tempi dal log (backup.last registra anche la pulizia delle versioni successiva).
		if len(runs) > 0 {
			end := runs[0].end
			j.LastEnd = &end
			if runs[0].spent > 0 {
				start := end.Add(-runs[0].spent)
				j.LastStart = &start
			}
		} else if t := epoch(lv["end_time"]); t != nil {
			j.LastEnd = t
		}
		j.LastSuccess = epoch(lv["last_backup_success_time"])
		j.TransferredBytes = int64p(lv["target_size"])
		if v := int64p(lv["total_size"]); v != nil {
			details["source_bytes"] = *v
		}
		if v := int64p(lv["processed_size"]); v != nil {
			details["processed_bytes"] = *v
		}
		if v := lv["last_backup_success_version"]; v != "" {
			details["last_version"] = v
		}
		if dv := detect[id]; dv != nil {
			dres, dmsg := lastResult(dv)
			details["integrity_check"] = map[string]any{"end": epoch(dv["end_time"]), "result": dres, "error": dmsg}
		}

		history := []map[string]any{}
		for i, r := range runs {
			if i >= 60 {
				break
			}
			h := map[string]any{"id": id + ":" + strconv.FormatInt(r.end.Unix(), 10), "end": r.end, "result": r.result,
				"state": "Stopped", "retry": false, "reason": r.reason}
			if r.spent > 0 {
				h["start"] = r.end.Add(-r.spent)
			}
			if i == 0 {
				// L'esito ufficiale dell'ultima esecuzione è quello di backup.last.
				h["result"] = result
				if reason != "" {
					h["reason"] = reason
				}
				if j.TransferredBytes != nil {
					h["backup_bytes"] = *j.TransferredBytes
				}
			}
			history = append(history, h)
		}
		details["history"] = history
		if len(runs) >= 3 {
			var gaps []float64
			for i := 0; i+1 < len(runs) && i < 10; i++ {
				gaps = append(gaps, runs[i].end.Sub(runs[i+1].end).Hours())
			}
			sort.Float64s(gaps)
			details["expected_interval_h"] = float64(int(gaps[len(gaps)/2]*10)) / 10
		}
		j.Details = details
		p.Jobs = append(p.Jobs, j)
	}
	return p
}

// Report: i task di Hyper Backup (se installato) su /agent/v1/hyperbackup.
func (c *Collector) Report(ctx context.Context) (string, any, error) {
	if !c.hasHyperBackup() {
		return "", nil, nil
	}
	return "/agent/v1/hyperbackup", c.hyperBackup(ctx), nil
}
