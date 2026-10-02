// Saving a file the page made (CSV, project export). In the desktop app the page runs in pywebview, whose
// WebView2 ignores <a download> links, so the file goes through the app's bridge: a native Save As dialog.
// In a browser it's an ordinary download.

export function toCsv(columns, rows) {
  const esc = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  // A byte-order mark so Excel reads names with accents (Jos Buttler is fine; Sébastien isn't without it).
  return "﻿" + [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\r\n");
}

export function safeFilename(name, ext) {
  const base = String(name || "doosra").replace(/[\\/:*?"<>|—]+/g, "-").replace(/\s+/g, " ").trim().slice(0, 80);
  return `${base || "doosra"}.${ext}`;
}

// Resolves to true when saved, false when the user cancelled the dialog.
export async function saveFile(filename, text, mime = "text/csv") {
  const bridge = window.pywebview?.api;
  if (bridge?.save_file) {
    const path = await bridge.save_file(filename, text);
    return Boolean(path);
  }
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename, style: "display:none" });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);    // revoking at once can cancel the download
  return true;
}

export function saveCsv(name, columns, rows) {
  return saveFile(safeFilename(name, "csv"), toCsv(columns, rows));
}
