//go:build windows

// Package power impedisce la sospensione automatica per inattività mentre è attiva la
// finestra di backup. Non tocca spegnimento, sospensione scelta dall'utente o chiusura
// del coperchio: quelle restano sempre all'utente.
package power

import (
	"runtime"
	"sync"

	"golang.org/x/sys/windows"
)

var (
	kernel32                    = windows.NewLazySystemDLL("kernel32.dll")
	procSetThreadExecutionState = kernel32.NewProc("SetThreadExecutionState")
)

const (
	esContinuous     = 0x80000000
	esSystemRequired = 0x00000001
)

// Lo stato vale per il thread che lo imposta: un thread dedicato riceve le richieste.
var (
	once sync.Once
	reqs chan bool
)

// KeepAwake(true) tiene il sistema sveglio finché non si chiama KeepAwake(false).
func KeepAwake(on bool) {
	once.Do(func() {
		reqs = make(chan bool, 1)
		go func() {
			runtime.LockOSThread()
			for on := range reqs {
				flags := uintptr(esContinuous)
				if on {
					flags |= esSystemRequired
				}
				procSetThreadExecutionState.Call(flags)
			}
		}()
	})
	select {
	case reqs <- on:
	default: // una richiesta è già in coda: la sostituiamo con l'ultima
		select {
		case <-reqs:
		default:
		}
		reqs <- on
	}
}
