package qnap

import (
	"regexp"
	"strconv"
	"strings"
)

var (
	reNumber = regexp.MustCompile(`-?\d+(\.\d+)?`)
	// Riga di stato md: "... [2/2] [UU]" oppure "[2/1] [U_]".
	reMdStatus = regexp.MustCompile(`\[(\d+)/(\d+)\]\s+\[([U_]+)\]`)
)

// md di sistema QNAP (firmware, swap, cache): contengono slot di tutti gli alloggiamenti
// e risultano "degradati" per costruzione quando il NAS non è pieno.
var systemMD = map[string]bool{"md9": true, "md13": true, "md256": true, "md321": true, "md322": true}

// parseTemp interpreta "43 C/109 F", "43", "43°C". "--" o vuoto = non disponibile.
func parseTemp(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" || strings.HasPrefix(s, "--") {
		return 0, false
	}
	m := reNumber.FindString(s)
	if m == "" {
		return 0, false
	}
	v, err := strconv.ParseFloat(m, 64)
	if err != nil || v <= 0 || v > 150 {
		return 0, false
	}
	return v, true
}

// parseRPM interpreta "1234 RPM". 0 RPM è un valore valido (ventola ferma).
func parseRPM(s string) (float64, bool) {
	m := reNumber.FindString(s)
	if m == "" {
		return 0, false
	}
	v, err := strconv.ParseFloat(m, 64)
	return v, err == nil && v >= 0
}

// parseSmart ritorna (ok, riconosciuto). I valori visti sui QTS sono GOOD/Normal/Warning/Abnormal.
func parseSmart(s string) (bool, bool) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "good", "normal", "ok":
		return true, true
	case "warning", "abnormal", "bad", "error", "failed":
		return false, true
	}
	return false, false
}

func isEmptySlot(s string) bool {
	s = strings.TrimSpace(s)
	return s == "" || strings.HasPrefix(s, "--") || strings.EqualFold(s, "no disk")
}

func parseMdstat(s string) []Array {
	var out []Array
	var cur string
	for _, line := range strings.Split(s, "\n") {
		if name, _, ok := strings.Cut(line, " : "); ok && strings.HasPrefix(name, "md") {
			cur = strings.TrimSpace(name)
			continue
		}
		if cur == "" {
			continue
		}
		m := reMdStatus.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		if !systemMD[cur] {
			out = append(out, Array{Name: cur, Healthy: !strings.Contains(m[3], "_") && m[1] == m[2], Detail: m[0]})
		}
		cur = ""
	}
	return out
}

func parseZpoolList(s string) []Array {
	var out []Array
	for _, line := range strings.Split(strings.TrimSpace(s), "\n") {
		f := strings.Fields(line)
		if len(f) < 2 {
			continue
		}
		out = append(out, Array{Name: f[0], Healthy: f[1] == "ONLINE", Detail: f[1]})
	}
	return out
}
