package window

import (
	"path/filepath"
	"testing"
	"time"
)

var rome, _ = time.LoadLocation("Europe/Rome")

func at(s string) time.Time {
	t, err := time.ParseInLocation("2006-01-02 15:04", s, rome)
	if err != nil {
		panic(err)
	}
	return t
}

// Dal giovedì alle 18:00 al lunedì alle 9:00. Il 1° ottobre 2026 è un giovedì.
var thuMon = &Window{Intervals: []Interval{{Start: Point{4, "18:00"}, End: Point{1, "09:00"}}}}

func TestActiveAcrossWeekend(t *testing.T) {
	cases := map[string]bool{
		"2026-10-01 17:59": false, // giovedì, prima
		"2026-10-01 18:00": true,
		"2026-10-03 03:00": true,  // sabato notte
		"2026-10-05 08:59": true,  // lunedì
		"2026-10-05 09:00": false, // fine
		"2026-10-06 12:00": false, // martedì
	}
	for s, want := range cases {
		got, until := thuMon.Active(at(s))
		if got != want {
			t.Errorf("%s: attivo=%v, atteso %v", s, got, want)
		}
		if got && !until.Equal(at("2026-10-05 09:00")) {
			t.Errorf("%s: fino a %v", s, until)
		}
	}
}

func TestSameDayAndInvalid(t *testing.T) {
	w := &Window{Intervals: []Interval{
		{Start: Point{3, "20:00"}, End: Point{3, "23:30"}},
		{Start: Point{9, "20:00"}, End: Point{3, "23:30"}}, // giorno non valido: ignorato
		{Start: Point{2, "10:00"}, End: Point{2, "10:00"}}, // durata zero: ignorato
	}}
	if ok, _ := w.Active(at("2026-09-30 21:00")); !ok { // mercoledì
		t.Fatal("atteso attivo")
	}
	if ok, _ := w.Active(at("2026-09-29 10:00")); ok {
		t.Fatal("intervallo di durata zero non deve valere")
	}
	var none *Window
	if ok, _ := none.Active(time.Now()); ok {
		t.Fatal("nessuna finestra")
	}
}

func TestSaveLoad(t *testing.T) {
	p := filepath.Join(t.TempDir(), "w.json")
	if err := Save(p, thuMon); err != nil {
		t.Fatal(err)
	}
	if w := Load(p); w == nil || len(w.Intervals) != 1 {
		t.Fatal("finestra non riletta")
	}
	if err := Save(p, nil); err != nil || Load(p) != nil {
		t.Fatal("la finestra rimossa deve sparire")
	}
}
