// Package window valuta la finestra di backup dei PC: intervalli settimanali (es. dal
// giovedì alle 18:00 al lunedì alle 9:00) nell'ora locale del PC, durante i quali
// l'agente tiene il PC disponibile per il backup (niente sospensione automatica) e lo
// segnala all'utente nell'icona della tray.
package window

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Point è un istante della settimana: giorno ISO (1 = lunedì … 7 = domenica) e ora "HH:MM".
type Point struct {
	Day  int    `json:"day"`
	Time string `json:"time"`
}

type Interval struct {
	Start Point `json:"start"`
	End   Point `json:"end"`
}

// Window è la finestra effettiva inviata dalla console (nil = nessuna finestra).
type Window struct {
	Intervals []Interval `json:"intervals"`
	// Minuti prima dell'inizio in cui l'icona ricorda all'utente di lasciare acceso il PC
	// (0 = nessun promemoria).
	RemindBeforeMin int `json:"remind_before_minutes,omitempty"`
}

const week = 7 * 24 * 60

// minuteOfWeek: minuti dal lunedì alle 00:00.
func (p Point) minuteOfWeek() (int, error) {
	if p.Day < 1 || p.Day > 7 {
		return 0, fmt.Errorf("giorno %d non valido", p.Day)
	}
	h, m, ok := strings.Cut(p.Time, ":")
	hh, err1 := strconv.Atoi(h)
	mm, err2 := strconv.Atoi(m)
	if !ok || err1 != nil || err2 != nil || hh < 0 || hh > 23 || mm < 0 || mm > 59 {
		return 0, fmt.Errorf("ora %q non valida", p.Time)
	}
	return (p.Day-1)*24*60 + hh*60 + mm, nil
}

func nowMinute(t time.Time) int {
	wd := (int(t.Weekday()) + 6) % 7 // lunedì = 0
	return wd*24*60 + t.Hour()*60 + t.Minute()
}

// Active dice se t cade in uno degli intervalli e fino a quando (fine dell'intervallo,
// nell'ora locale di t). Un intervallo può scavalcare la domenica (giovedì → lunedì).
func (w *Window) Active(t time.Time) (bool, time.Time) {
	if w == nil {
		return false, time.Time{}
	}
	now := nowMinute(t)
	var until time.Time
	active := false
	for _, iv := range w.Intervals {
		s, err1 := iv.Start.minuteOfWeek()
		e, err2 := iv.End.minuteOfWeek()
		if err1 != nil || err2 != nil || s == e {
			continue
		}
		length := (e - s + week) % week
		elapsed := (now - s + week) % week
		if elapsed >= length {
			continue
		}
		end := t.Truncate(time.Minute).Add(time.Duration(length-elapsed) * time.Minute)
		if !active || end.After(until) {
			until = end
		}
		active = true
	}
	return active, until
}

// Upcoming: se t cade nel periodo di promemoria prima di un intervallo, ne ritorna
// inizio e fine (il più vicino). Durante un intervallo già in corso vale Active.
func (w *Window) Upcoming(t time.Time) (start, end time.Time, ok bool) {
	if w == nil || w.RemindBeforeMin <= 0 {
		return
	}
	if active, _ := w.Active(t); active {
		return
	}
	now := nowMinute(t)
	best := -1
	for _, iv := range w.Intervals {
		s, err1 := iv.Start.minuteOfWeek()
		e, err2 := iv.End.minuteOfWeek()
		if err1 != nil || err2 != nil || s == e {
			continue
		}
		wait := (s - now + week) % week
		if wait == 0 || wait > w.RemindBeforeMin || (best >= 0 && wait >= best) {
			continue
		}
		best = wait
		start = t.Truncate(time.Minute).Add(time.Duration(wait) * time.Minute)
		end = start.Add(time.Duration((e-s+week)%week) * time.Minute)
		ok = true
	}
	return
}

// Load e Save conservano l'ultima finestra ricevuta: vale anche se la console non è
// raggiungibile (es. rete giù proprio durante il fine settimana).
func Load(path string) *Window {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var w Window
	if json.Unmarshal(b, &w) != nil || len(w.Intervals) == 0 {
		return nil
	}
	return &w
}

func Save(path string, w *Window) error {
	if w == nil || len(w.Intervals) == 0 {
		err := os.Remove(path)
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	b, _ := json.Marshal(w)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
