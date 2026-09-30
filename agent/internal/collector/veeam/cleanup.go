package veeam

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

//go:embed scripts/cleanup_common.ps1
var cleanupCommonPS string

//go:embed scripts/vbr_cleanup_scan.ps1
var cleanupScanPS string

//go:embed scripts/vbr_cleanup_delete.ps1
var cleanupDeletePS string

// MinOlderThanDays: sotto questa soglia la pulizia non è permessa (stessa regola della console).
const MinOlderThanDays = 7

var guidRe = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

func (c *Collector) IsVBR() bool { return c.product == "vbr" }

func (c *Collector) Product() string { return c.product }

func checkDays(days int) error {
	if days < MinOlderThanDays {
		return fmt.Errorf("la soglia minima è %d giorni", MinOlderThanDays)
	}
	return nil
}

// CleanupScan elenca i backup con l'indicazione di quali sono eliminabili (solo lettura).
func (c *Collector) CleanupScan(ctx context.Context, olderThanDays int) (map[string]any, error) {
	if err := checkDays(olderThanDays); err != nil {
		return nil, err
	}
	script := fmt.Sprintf("%s\n%s\n$OlderThanDays = %d\n%s", commonPS, cleanupCommonPS, olderThanDays, cleanupScanPS)
	return c.runJSON(ctx, script)
}

// CleanupDelete elimina tramite Veeam i backup indicati; lo script li ricontrolla uno per uno.
func (c *Collector) CleanupDelete(ctx context.Context, olderThanDays int, backupIDs []string) (map[string]any, error) {
	if err := checkDays(olderThanDays); err != nil {
		return nil, err
	}
	if len(backupIDs) == 0 {
		return nil, errors.New("nessun backup indicato")
	}
	quoted := make([]string, 0, len(backupIDs))
	for _, id := range backupIDs {
		// Gli id finiscono dentro lo script: accettiamo solo GUID.
		if !guidRe.MatchString(id) {
			return nil, fmt.Errorf("id di backup non valido: %q", id)
		}
		quoted = append(quoted, "'"+id+"'")
	}
	script := fmt.Sprintf("%s\n%s\n$OlderThanDays = %d\n$BackupIds = @(%s)\n%s",
		commonPS, cleanupCommonPS, olderThanDays, strings.Join(quoted, ","), cleanupDeletePS)
	return c.runJSON(ctx, script)
}

func (c *Collector) runJSON(ctx context.Context, script string) (map[string]any, error) {
	out, err := c.run(ctx, script)
	if err != nil {
		return nil, err
	}
	if i := strings.IndexByte(string(out), '{'); i > 0 {
		out = out[i:]
	}
	var res map[string]any
	if err := json.Unmarshal(out, &res); err != nil {
		return nil, fmt.Errorf("output dello script non valido: %w", err)
	}
	return res, nil
}
