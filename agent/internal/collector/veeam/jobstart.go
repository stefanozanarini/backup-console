package veeam

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"strings"
)

//go:embed scripts/vbr_job_start.ps1
var vbrJobStartPS string

//go:embed scripts/vaw_job_start.ps1
var vawJobStartPS string

// StartJob avvia un job Veeam: su B&R quello indicato (mode "retry" = solo gli oggetti
// falliti, "start" = esecuzione completa), su Veeam Agent il job configurato.
func (c *Collector) StartJob(ctx context.Context, jobKey, jobName, mode string) (map[string]any, error) {
	if mode != "retry" && mode != "start" {
		return nil, fmt.Errorf("modalità %q non valida", mode)
	}
	switch c.product {
	case "vbr":
		// L'id finisce dentro lo script: solo GUID.
		if !guidRe.MatchString(jobKey) {
			return nil, fmt.Errorf("id di job non valido: %q", jobKey)
		}
		script := fmt.Sprintf("%s\n$JobId = '%s'\n$Mode = '%s'\n%s", commonPS, jobKey, mode, vbrJobStartPS)
		return c.runJSON(ctx, script)
	case "vaw":
		if !strings.HasPrefix(jobKey, "vaw:") {
			return nil, fmt.Errorf("id di job non valido: %q", jobKey)
		}
		// Il nome serve solo nel risultato: niente apici, così resta una stringa innocua.
		name := strings.Map(func(r rune) rune {
			if r == '\'' || r < ' ' {
				return -1
			}
			return r
		}, jobName)
		script := fmt.Sprintf("%s\n$JobName = '%s'\n%s", commonPS, name, vawJobStartPS)
		return c.runJSON(ctx, script)
	}
	return nil, errors.New("prodotto Veeam non supportato")
}
