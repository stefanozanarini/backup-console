//go:build windows

// Package tray è l'icona dell'agente nell'area di notifica di Windows. Gira nella
// sessione dell'utente (il servizio non può mostrare nulla sul desktop) e legge lo
// stato dal file scritto dal servizio.
package tray

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
	"time"

	"fyne.io/systray"
	"golang.org/x/sys/windows"

	"backupconsole/agent/internal/status"
	"backupconsole/agent/internal/version"
)

//go:embed icon.ico
var iconOK []byte

//go:embed icon-gray.ico
var iconOff []byte

const (
	refreshEvery = 15 * time.Second
	// Il servizio aggiorna lo stato a ogni heartbeat (60 s): oltre questo è fermo.
	staleAfter = 3 * time.Minute
)

// Run mostra l'icona finché l'utente non sceglie "Esci" o termina la sessione.
func Run() error {
	// Una sola icona per sessione utente.
	name, _ := windows.UTF16PtrFromString(`Local\BackupConsoleAgentTray`)
	mutex, err := windows.CreateMutex(nil, false, name)
	if err == windows.ERROR_ALREADY_EXISTS {
		return nil
	}
	if err != nil {
		return err
	}
	defer windows.CloseHandle(mutex)

	systray.Run(func() { onReady(mutex) }, func() {})
	return nil
}

type menu struct {
	title, state, contact, exec *systray.MenuItem
	open, quit                  *systray.MenuItem
	server                      string
}

func onReady(mutex windows.Handle) {
	systray.SetIcon(iconOff)
	systray.SetTooltip("Backup Console Agent")
	m := &menu{
		title:   systray.AddMenuItem("Backup Console Agent "+version.Version, ""),
		state:   systray.AddMenuItem("Stato: in lettura…", ""),
		contact: systray.AddMenuItem("Ultimo contatto: —", ""),
		exec:    systray.AddMenuItem("Esecuzione remota: —", ""),
	}
	for _, it := range []*systray.MenuItem{m.title, m.state, m.contact, m.exec} {
		it.Disable()
	}
	systray.AddSeparator()
	m.open = systray.AddMenuItem("Apri la console", "Apre la Backup Console nel browser")
	m.quit = systray.AddMenuItem("Nascondi icona", "Chiude solo l'icona: l'agente continua a funzionare")

	m.refresh(mutex)
	go func() {
		t := time.NewTicker(refreshEvery)
		defer t.Stop()
		for {
			select {
			case <-t.C:
				m.refresh(mutex)
			case <-m.open.ClickedCh:
				if m.server != "" {
					openURL(m.server)
				}
			case <-m.quit.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()
}

func (m *menu) refresh(mutex windows.Handle) {
	st, err := status.Read(status.Path())
	switch {
	case err != nil:
		m.show(false, "Stato: agente non installato o non ancora avviato", "", "")
		return
	case time.Since(st.UpdatedAt) > staleAfter:
		m.show(false, "Stato: servizio dell'agente fermo", contactLine(st), execLine(st))
		return
	}
	m.server = st.Server

	// Dopo un aggiornamento dell'agente anche questo eseguibile è stato sostituito:
	// ci rilanciamo per usare la nuova versione.
	if st.Version != "" && st.Version != version.Version && version.Version != "dev" {
		if exe, err := os.Executable(); err == nil {
			windows.CloseHandle(mutex)
			if exec.Command(exe).Start() == nil {
				systray.Quit()
				return
			}
		}
	}

	switch st.State {
	case "approved":
		m.show(true, "Stato: connesso alla console", contactLine(st), execLine(st))
	case "pending":
		m.show(false, "Stato: in attesa di approvazione in console", contactLine(st), execLine(st))
	case "revoked":
		m.show(false, "Stato: agente revocato dalla console", contactLine(st), execLine(st))
	default:
		msg := "Stato: console non raggiungibile"
		if st.LastError != "" {
			msg += " (" + truncate(st.LastError, 60) + ")"
		}
		m.show(false, msg, contactLine(st), execLine(st))
	}
}

func (m *menu) show(ok bool, state, contact, exec string) {
	if ok {
		systray.SetIcon(iconOK)
	} else {
		systray.SetIcon(iconOff)
	}
	systray.SetTooltip("Backup Console Agent – " + state[len("Stato: "):])
	m.state.SetTitle(state)
	if contact != "" {
		m.contact.SetTitle(contact)
	}
	if exec != "" {
		m.exec.SetTitle(exec)
	}
}

func contactLine(st status.Status) string {
	if st.LastContact.IsZero() {
		return "Ultimo contatto: mai"
	}
	return fmt.Sprintf("Ultimo contatto: %s", st.LastContact.Local().Format("02/01 15:04"))
}

func execLine(st status.Status) string {
	if st.RemoteExec {
		return "Esecuzione remota: consentita"
	}
	return "Esecuzione remota: disattivata su questo PC"
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

func openURL(url string) {
	verb, _ := windows.UTF16PtrFromString("open")
	target, _ := windows.UTF16PtrFromString(url)
	_ = windows.ShellExecute(0, verb, target, nil, nil, windows.SW_SHOWNORMAL)
}
