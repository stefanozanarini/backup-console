import { useSyncExternalStore } from "react";

// Preferenze di visualizzazione dell'operatore, salvate solo in questo browser.

const HIDE_URANIUM_KEY = "bc.hideUranium";
const EVENT = "bc-prefs";

function read(): boolean {
  try {
    return localStorage.getItem(HIDE_URANIUM_KEY) === "1";
  } catch {
    return false;
  }
}

// Valore in memoria quando localStorage non è disponibile (finestra privata, storage bloccato).
let memory = false;

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", cb);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

/** Nasconde job e problemi di Uranium Backup nelle pagine Problemi e Backup e nel contatore. */
export function useHideUranium(): [boolean, (v: boolean) => void] {
  const value = useSyncExternalStore(subscribe, () => (storageWorks() ? read() : memory));
  const set = (v: boolean) => {
    memory = v;
    try {
      if (v) localStorage.setItem(HIDE_URANIUM_KEY, "1");
      else localStorage.removeItem(HIDE_URANIUM_KEY);
    } catch {
      /* storage non disponibile: vale fino al reload */
    }
    window.dispatchEvent(new Event(EVENT));
  };
  return [value, set];
}

function storageWorks(): boolean {
  try {
    localStorage.getItem(HIDE_URANIUM_KEY);
    return true;
  } catch {
    return false;
  }
}

/** Parametro per le API dei problemi. */
export function hideQuery(hideUranium: boolean): string {
  return hideUranium ? "hide=uranium" : "";
}
