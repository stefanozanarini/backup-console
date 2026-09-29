//go:build windows

package system

import (
	"strconv"

	"golang.org/x/sys/windows/registry"
)

// supremoKeys: Supremo (NanoSystems) è a 32 bit, quindi di norma sotto WOW6432Node.
var supremoKeys = []string{`SOFTWARE\WOW6432Node\Supremo`, `SOFTWARE\Supremo`}

// remoteAccess riporta gli ID degli strumenti di assistenza remota installati.
func remoteAccess() map[string]string {
	out := map[string]string{}
	for _, path := range supremoKeys {
		k, err := registry.OpenKey(registry.LOCAL_MACHINE, path, registry.QUERY_VALUE)
		if err != nil {
			continue
		}
		id := readID(k, "ClientID")
		_ = k.Close()
		if id != "" {
			out["supremo_id"] = id
			break
		}
	}
	return out
}

// readID accetta il valore sia come stringa sia come numero (dipende dalla versione).
func readID(k registry.Key, name string) string {
	if s, _, err := k.GetStringValue(name); err == nil {
		return cleanID(s)
	}
	if n, _, err := k.GetIntegerValue(name); err == nil && n > 0 {
		return strconv.FormatUint(n, 10)
	}
	return ""
}
