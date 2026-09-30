// Esportazione di una tabella in un file che Excel (impostazioni italiane) apre con un
// doppio clic: CSV con separatore ";", BOM UTF-8 per gli accenti, righe CRLF.
export type Cell = string | number | boolean | Date | null | undefined;

function cell(v: Cell): string {
  if (v === null || v === undefined) return "";
  let s: string;
  if (v instanceof Date) {
    s = v.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });
  } else if (typeof v === "number") {
    s = Number.isInteger(v) ? String(v) : v.toLocaleString("it-IT", { useGrouping: false });
  } else if (typeof v === "boolean") {
    s = v ? "sì" : "no";
  } else {
    s = v;
    // Evita che Excel interpreti come formula un testo che inizia con = + - @.
    if (/^[=+\-@]/.test(s)) s = "'" + s;
  }
  return /[";\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function exportTable(filename: string, headers: string[], rows: Cell[][]): void {
  const lines = [headers, ...rows].map((r) => r.map(cell).join(";"));
  const blob = new Blob(["﻿" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}
