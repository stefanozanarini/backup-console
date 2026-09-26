//go:build windows

// Package winsvc registra e fa girare l'agente come servizio Windows.
package winsvc

import (
	"context"
	"errors"
	"fmt"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const (
	Name        = "BackupConsoleAgent"
	DisplayName = "Backup Console Agent"
	description = "Agente di monitoring e gestione della Backup Console (Veeam, spazio disco, comandi remoti)."
)

func IsService() bool {
	ok, err := svc.IsWindowsService()
	return err == nil && ok
}

type handler struct {
	run func(ctx context.Context) error
	// exitCode != 0 fa scattare le azioni di ripristino (riavvio) configurate da Install.
	exitCode uint32
}

func (h *handler) Execute(_ []string, req <-chan svc.ChangeRequest, st chan<- svc.Status) (bool, uint32) {
	st <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- h.run(ctx) }()
	st <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case err := <-done:
			cancel()
			if err != nil {
				return true, h.exitCode
			}
			return false, 0
		case c := <-req:
			switch c.Cmd {
			case svc.Interrogate:
				st <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				st <- svc.Status{State: svc.StopPending}
				cancel()
				select {
				case <-done:
				case <-time.After(20 * time.Second):
				}
				return false, 0
			}
		}
	}
}

// Run esegue run() sotto il Service Control Manager. Se run ritorna un errore il
// servizio termina con exitCode e Windows lo riavvia (es. dopo un aggiornamento).
func Run(run func(ctx context.Context) error, exitCode uint32) error {
	return svc.Run(Name, &handler{run: run, exitCode: exitCode})
}

// Install crea (o aggiorna) il servizio con avvio automatico e riavvio in caso di uscita.
func Install(exe string, args ...string) error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connessione al Service Control Manager (servono privilegi di amministratore): %w", err)
	}
	defer m.Disconnect()

	s, err := m.OpenService(Name)
	if err == nil {
		cfg, _ := s.Config()
		cfg.BinaryPathName = syscallQuote(exe, args)
		cfg.StartType = mgr.StartAutomatic
		cfg.DisplayName, cfg.Description = DisplayName, description
		if err := s.UpdateConfig(cfg); err != nil {
			s.Close()
			return err
		}
	} else {
		s, err = m.CreateService(Name, exe, mgr.Config{
			DisplayName: DisplayName, Description: description, StartType: mgr.StartAutomatic,
		}, args...)
		if err != nil {
			return err
		}
	}
	defer s.Close()
	actions := []mgr.RecoveryAction{
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 15 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 60 * time.Second},
	}
	if err := s.SetRecoveryActions(actions, 86400); err != nil {
		return err
	}
	return s.SetRecoveryActionsOnNonCrashFailures(true)
}

func syscallQuote(exe string, args []string) string {
	out := windows.EscapeArg(exe)
	for _, a := range args {
		out += " " + windows.EscapeArg(a)
	}
	return out
}

func Start() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return err
	}
	defer s.Close()
	return s.Start()
}

// Stop ferma il servizio e attende che sia fermo (massimo 30 secondi).
func Stop() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return nil // non installato
	}
	defer s.Close()
	status, err := s.Control(svc.Stop)
	if err != nil {
		if errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
			return nil
		}
		return err
	}
	for deadline := time.Now().Add(30 * time.Second); status.State != svc.Stopped && time.Now().Before(deadline); {
		time.Sleep(500 * time.Millisecond)
		if status, err = s.Query(); err != nil {
			return err
		}
	}
	return nil
}

func Remove() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(Name)
	if err != nil {
		return nil
	}
	defer s.Close()
	return s.Delete()
}
