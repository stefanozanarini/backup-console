package agent

import (
	"context"
	"encoding/json"
	"log"
	"path/filepath"
	"sync"
	"time"

	"backupconsole/agent/internal/power"
	"backupconsole/agent/internal/window"
)

// Finestra di backup dei PC: la console la invia con la risposta all'heartbeat (nil se
// il dispositivo non ne ha una o non è un PC); l'agente la tiene anche su disco.
type backupWindow struct {
	mu     sync.Mutex
	path   string
	w      *window.Window
	active bool
	until  time.Time
}

func newBackupWindow(dataDir string) *backupWindow {
	p := filepath.Join(dataDir, "backup_window.json")
	return &backupWindow{path: p, w: window.Load(p)}
}

// set aggiorna la finestra se è cambiata (risposta dell'heartbeat).
func (b *backupWindow) set(w *window.Window) {
	b.mu.Lock()
	defer b.mu.Unlock()
	old, _ := json.Marshal(b.w)
	cur, _ := json.Marshal(w)
	if string(old) == string(cur) {
		return
	}
	b.w = w
	if err := window.Save(b.path, w); err != nil {
		log.Printf("finestra di backup: salvataggio: %v", err)
	}
	if w == nil {
		log.Printf("finestra di backup: nessuna")
	} else {
		log.Printf("finestra di backup: %s", cur)
	}
}

// tick valuta la finestra e applica o toglie il "tieni sveglio".
func (b *backupWindow) tick(now time.Time) {
	b.mu.Lock()
	defer b.mu.Unlock()
	active, until := b.w.Active(now)
	if active != b.active {
		if active {
			log.Printf("finestra di backup iniziata (fino a %s): sospensione automatica disattivata", until.Format("02/01 15:04"))
		} else {
			log.Printf("finestra di backup terminata: sospensione automatica di nuovo consentita")
		}
		power.KeepAwake(active)
	}
	b.active, b.until = active, until
}

func (b *backupWindow) state() (bool, time.Time) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.active, b.until
}

// upcoming: finestra che sta per iniziare (periodo di promemoria).
func (b *backupWindow) upcoming(now time.Time) (time.Time, time.Time, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.w.Upcoming(now)
}

func (b *backupWindow) loop(ctx context.Context) {
	b.tick(time.Now())
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			power.KeepAwake(false)
			return
		case now := <-t.C:
			b.tick(now)
		}
	}
}
