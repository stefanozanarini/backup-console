package agent

import (
	"os"
	"os/exec"
	"regexp"
	"strings"
)

// Sui QNAP l'auto-aggiornamento sostituisce il binario senza passare da QTS: la
// versione registrata in qpkg.conf (quella mostrata da App Center) resta quella
// dell'installazione. All'avvio la si allinea a quella in esecuzione.

const qpkgConf = "/etc/config/qpkg.conf"

var qpkgNameRe = regexp.MustCompile(`^[A-Za-z0-9_]{1,64}$`)

type cfgRunner func(name string, args ...string) (string, error)

func execCfg(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).Output()
	return string(out), err
}

func syncQPKGVersion(running string, run cfgRunner, logf func(string, ...any)) {
	pkg := os.Getenv("QNAP_QPKG") // impostata da bc-agent.sh
	if pkg == "" || !qpkgNameRe.MatchString(pkg) || running == "" || running == "dev" {
		return
	}
	cur, err := run("/sbin/getcfg", pkg, "Version", "-f", qpkgConf)
	if err != nil {
		return
	}
	cur = strings.TrimSpace(cur)
	if cur == running {
		return
	}
	if _, err := run("/sbin/setcfg", pkg, "Version", running, "-f", qpkgConf); err != nil {
		logf("versione in App Center non aggiornata: %v", err)
		return
	}
	logf("versione in App Center aggiornata: %s → %s", cur, running)
}
