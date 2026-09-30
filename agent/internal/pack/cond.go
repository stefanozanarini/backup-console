package pack

import (
	"os"
	"regexp"
	"strings"
)

// Cond: quando un probe si applica. Valutata dall'agente, senza eseguire codice:
//
//	{"service": "VeeamBackupSvc"}            servizio Windows installato
//	{"file": "%ProgramData%\\X\\a.exe"}       file presente (variabili %NOME% o $NOME)
//	{"dir": "/var/packages/HyperBackup"}      cartella presente
//	{"file_contains": {"path": "/etc.defaults/VERSION", "text": "os_name=\"DSM\""}}
//	{"all": [...]}, {"any": [...]}, {"not": {...}}
type Cond struct {
	Service      string        `json:"service,omitempty"`
	File         string        `json:"file,omitempty"`
	Dir          string        `json:"dir,omitempty"`
	FileContains *FileContains `json:"file_contains,omitempty"`
	All          []Cond        `json:"all,omitempty"`
	Any          []Cond        `json:"any,omitempty"`
	Not          *Cond         `json:"not,omitempty"`
}

type FileContains struct {
	Path string `json:"path"`
	Text string `json:"text"`
}

// Env: accesso al sistema, sostituibile nei test.
type Env struct {
	ServiceInstalled func(string) bool
	Stat             func(string) (os.FileInfo, error)
	ReadFile         func(string) ([]byte, error)
	Getenv           func(string) string
}

var reWinVar = regexp.MustCompile(`%([A-Za-z0-9_()]+)%`)

func (e Env) expand(p string) string {
	p = reWinVar.ReplaceAllStringFunc(p, func(m string) string { return e.Getenv(m[1 : len(m)-1]) })
	return os.Expand(p, e.Getenv)
}

func (c *Cond) Eval(e Env) bool {
	if c == nil {
		return true
	}
	ok := true
	if c.Service != "" {
		ok = ok && e.ServiceInstalled != nil && e.ServiceInstalled(c.Service)
	}
	if c.File != "" {
		fi, err := e.Stat(e.expand(c.File))
		ok = ok && err == nil && !fi.IsDir()
	}
	if c.Dir != "" {
		fi, err := e.Stat(e.expand(c.Dir))
		ok = ok && err == nil && fi.IsDir()
	}
	if c.FileContains != nil {
		b, err := e.ReadFile(e.expand(c.FileContains.Path))
		ok = ok && err == nil && strings.Contains(string(b), c.FileContains.Text)
	}
	for i := range c.All {
		ok = ok && c.All[i].Eval(e)
	}
	if len(c.Any) > 0 {
		any := false
		for i := range c.Any {
			if c.Any[i].Eval(e) {
				any = true
				break
			}
		}
		ok = ok && any
	}
	if c.Not != nil {
		ok = ok && !c.Not.Eval(e)
	}
	return ok
}
