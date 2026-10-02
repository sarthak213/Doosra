import { useState } from "react";
import { saveCsv } from "./saveFile.js";

// "CSV" for any table or chart: `data()` returns {columns, rows} when clicked, so nothing is built until needed.
export default function CsvButton({ name, data, className = "ghost-btn" }) {
  const [state, setState] = useState(null);         // null | "saved" | "error"
  async function click() {
    try {
      const { columns, rows } = data();
      const saved = await saveCsv(name, columns, rows);
      setState(saved ? "saved" : null);
    } catch (e) {
      console.error("CSV export failed", e);
      setState("error");
    }
    setTimeout(() => setState(null), 2500);
  }
  return (
    <button type="button" className={className} onClick={click} title="Save the data as a CSV file">
      {state === "saved" ? "Saved" : state === "error" ? "Couldn't save" : "CSV"}
    </button>
  );
}
