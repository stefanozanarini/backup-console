package agent

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strings"
	"time"

	"backupconsole/agent/internal/client"
	"backupconsole/agent/internal/remote"
	"backupconsole/agent/internal/upgrade"
	"backupconsole/agent/internal/version"
)

// commandLoop ritira i comandi dalla console con un long-poll: la console risponde
// appena c'è qualcosa in coda, quindi la latenza è di circa un secondo.
func (a *Agent) commandLoop(ctx context.Context) {
	backoff := 5 * time.Second
	for ctx.Err() == nil {
		if !a.approved.Load() {
			sleep(ctx, 10*time.Second)
			continue
		}
		var cmd client.Command
		ok, err := a.client.GetJSON(ctx, "/agent/v1/commands/next?wait=25", &cmd)
		if err != nil {
			if ctx.Err() == nil {
				log.Printf("ritiro comandi: %v", err)
				sleep(ctx, backoff)
				backoff = min(backoff*2, 2*time.Minute)
			}
			continue
		}
		backoff = 5 * time.Second
		if !ok {
			continue
		}
		switch cmd.Type {
		case "exec":
			select {
			case a.execSlots <- struct{}{}:
				go func() {
					defer func() { <-a.execSlots }()
					a.runExec(ctx, cmd)
				}()
			case <-ctx.Done():
				return
			}
		case "upgrade":
			a.runUpgrade(ctx, cmd)
		case "veeam_job_start":
			select {
			case a.execSlots <- struct{}{}:
				go func() {
					defer func() { <-a.execSlots }()
					a.runVeeamJobStart(ctx, cmd)
				}()
			case <-ctx.Done():
				return
			}
		case "veeam_scan", "veeam_cleanup":
			select {
			case a.execSlots <- struct{}{}:
				go func() {
					defer func() { <-a.execSlots }()
					a.runVeeamCleanup(ctx, cmd)
				}()
			case <-ctx.Done():
				return
			}
		default:
			a.finish(ctx, cmd.ID, false, nil, "tipo di comando non supportato: "+cmd.Type)
		}
	}
}

func sleep(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}

func (a *Agent) output(ctx context.Context, id string) remote.Sink {
	return func(chunk string) {
		if err := a.client.Post(ctx, "/agent/v1/commands/"+id+"/output", map[string]string{"data": chunk}, nil); err != nil {
			log.Printf("comando %s: invio output: %v", id, err)
		}
	}
}

// finish comunica l'esito, riprovando qualche volta: se va perso il comando resta
// "in esecuzione" finché il worker della console non lo marca come timeout.
func (a *Agent) finish(ctx context.Context, id string, ok bool, exitCode *int, errMsg string) {
	a.finishWithResult(ctx, id, ok, exitCode, errMsg, nil)
}

func (a *Agent) finishWithResult(ctx context.Context, id string, ok bool, exitCode *int, errMsg string, result map[string]any) {
	res := client.CommandResult{Status: "failed", ExitCode: exitCode, Result: result}
	if ok {
		res.Status = "succeeded"
	}
	if errMsg != "" {
		res.Error = &errMsg
	}
	for attempt := 1; attempt <= 5; attempt++ {
		err := a.client.Post(ctx, "/agent/v1/commands/"+id+"/result", res, nil)
		if err == nil {
			return
		}
		var he *client.HTTPError
		if errors.As(err, &he) && he.Status < 500 {
			log.Printf("comando %s: esito rifiutato: %v", id, err)
			return
		}
		log.Printf("comando %s: invio esito (tentativo %d): %v", id, attempt, err)
		sleep(ctx, time.Duration(attempt)*5*time.Second)
	}
}

func (a *Agent) runExec(ctx context.Context, cmd client.Command) {
	if !a.cfg.RemoteExecAllowed() {
		a.finish(ctx, cmd.ID, false, nil, "esecuzione remota disabilitata su questo dispositivo")
		return
	}
	command, _ := cmd.Payload["command"].(string)
	shell, _ := cmd.Payload["shell"].(string)
	if strings.TrimSpace(command) == "" {
		a.finish(ctx, cmd.ID, false, nil, "comando vuoto")
		return
	}
	timeout := time.Duration(cmd.TimeoutS) * time.Second
	if timeout <= 0 {
		timeout = 5 * time.Minute
	}
	// Traccia locale sul dispositivo, indipendente dall'audit della console.
	log.Printf("comando %s ricevuto (shell %s, timeout %s): %s", cmd.ID, shell, timeout, truncate(command, 500))
	code, err := remote.Run(ctx, shell, command, timeout, a.output(ctx, cmd.ID))
	switch {
	case errors.Is(err, remote.ErrTimeout):
		a.finish(ctx, cmd.ID, false, nil, fmt.Sprintf("timeout dopo %s: processo terminato", timeout))
	case err != nil:
		a.finish(ctx, cmd.ID, false, nil, err.Error())
	default:
		log.Printf("comando %s terminato con codice %d", cmd.ID, code)
		a.finish(ctx, cmd.ID, code == 0, &code, "")
	}
}

func (a *Agent) runUpgrade(ctx context.Context, cmd client.Command) {
	req, err := upgrade.ParseRequest(cmd.Payload)
	if err != nil {
		a.finish(ctx, cmd.ID, false, nil, err.Error())
		return
	}
	sink := a.output(ctx, cmd.ID)
	logf := func(format string, args ...any) {
		msg := fmt.Sprintf(format, args...)
		log.Printf("aggiornamento %s: %s", req.Version, msg)
		sink(msg + "\n")
	}
	logf("da %s a %s (%s)", version.Version, req.Version, upgrade.Platform())
	actx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	err = upgrade.Apply(actx, a.client, req, upgrade.Options{
		CurrentVersion: version.Version, DataDir: a.cfg.DataDir, AllowUnsigned: a.cfg.AllowUnsignedUpgrades, Log: logf,
	})
	if err != nil {
		logf("fallito: %v", err)
		a.finish(ctx, cmd.ID, false, nil, err.Error())
		return
	}
	zero := 0
	a.finish(ctx, cmd.ID, true, &zero, "")
	a.requestRestart()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
