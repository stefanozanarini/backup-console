package agent

import (
	"context"
	"fmt"
	"log"
	"time"

	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/collector/veeam"
)

func (a *Agent) vbrCollector() *veeam.Collector {
	for _, c := range a.collectors {
		if v, ok := c.(*veeam.Collector); ok && v.IsVBR() {
			return v
		}
	}
	return nil
}

func intPayload(p map[string]any, key string) int {
	if f, ok := p[key].(float64); ok {
		return int(f)
	}
	return 0
}

// runVeeamCleanup esegue l'analisi (veeam_scan, solo lettura) o l'eliminazione
// (veeam_cleanup) dei backup Veeam decisi in console.
func (a *Agent) runVeeamCleanup(ctx context.Context, cmd client.Command) {
	vbr := a.vbrCollector()
	if vbr == nil {
		a.finish(ctx, cmd.ID, false, nil, "Veeam Backup & Replication non rilevato su questo dispositivo")
		return
	}
	timeout := time.Duration(cmd.TimeoutS) * time.Second
	if timeout <= 0 {
		timeout = 10 * time.Minute
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	days := intPayload(cmd.Payload, "older_than_days")
	sink := a.output(ctx, cmd.ID)

	switch cmd.Type {
	case "veeam_scan":
		log.Printf("comando %s: analisi backup Veeam (soglia %d giorni)", cmd.ID, days)
		sink(fmt.Sprintf("analisi dei backup Veeam più vecchi di %d giorni…\n", days))
		res, err := vbr.CleanupScan(cctx, days)
		if err != nil {
			a.finish(ctx, cmd.ID, false, nil, err.Error())
			return
		}
		n, cand, bytes := summarize(res)
		sink(fmt.Sprintf("backup analizzati: %d, eliminabili: %d (%.1f GB)\n", n, cand, float64(bytes)/(1<<30)))
		zero := 0
		a.finishWithResult(ctx, cmd.ID, true, &zero, "", res)

	case "veeam_cleanup":
		// Cancellare backup è un'azione distruttiva: vale il consenso locale del dispositivo.
		if !a.cfg.RemoteExecAllowed() {
			a.finish(ctx, cmd.ID, false, nil, "esecuzione remota disabilitata su questo dispositivo")
			return
		}
		raw, _ := cmd.Payload["backup_ids"].([]any)
		ids := make([]string, 0, len(raw))
		for _, v := range raw {
			if s, ok := v.(string); ok {
				ids = append(ids, s)
			}
		}
		log.Printf("comando %s: eliminazione di %d backup Veeam (soglia %d giorni): %v", cmd.ID, len(ids), days, ids)
		sink(fmt.Sprintf("eliminazione tramite Veeam di %d backup…\n", len(ids)))
		res, err := vbr.CleanupDelete(cctx, days, ids)
		if err != nil {
			a.finish(ctx, cmd.ID, false, nil, err.Error())
			return
		}
		deleted, skipped, failed := countResults(res)
		sink(fmt.Sprintf("eliminati: %d, saltati: %d, errori: %d\n", deleted, skipped, failed))
		code := 0
		if failed > 0 {
			code = 1
		}
		a.finishWithResult(ctx, cmd.ID, failed == 0, &code, "", res)
	}
}

func summarize(res map[string]any) (total, candidates int, bytes int64) {
	items, _ := res["items"].([]any)
	for _, it := range items {
		m, _ := it.(map[string]any)
		total++
		if c, _ := m["candidate"].(bool); c {
			candidates++
			if s, ok := m["size_bytes"].(float64); ok {
				bytes += int64(s)
			}
		}
	}
	return
}

func countResults(res map[string]any) (deleted, skipped, failed int) {
	results, _ := res["results"].([]any)
	for _, r := range results {
		m, _ := r.(map[string]any)
		switch m["status"] {
		case "deleted":
			deleted++
		case "error":
			failed++
		default:
			skipped++
		}
	}
	return
}
