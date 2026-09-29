package system

import "strings"

// cleanID tiene solo le cifre (Supremo può mostrare l'ID con spazi) e scarta valori
// palesemente non validi.
func cleanID(s string) string {
	s = strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, s)
	if len(s) < 6 || len(s) > 16 {
		return ""
	}
	return s
}
