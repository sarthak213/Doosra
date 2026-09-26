import { useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function InputBar({ onAsk, onCancel, disabled }) {
  const [value, setValue] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const debounceRef = useRef(null);

  useEffect(() => {
    if (disabled) {
      setSuggestions([]);
      return;
    }

    const words = value.trim().split(/\s+/);
    const tokenWords = words.slice(-2);
    const token = tokenWords.join(" ");
    if (token.length < 3) {
      setSuggestions([]);
      return;
    }

    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetch(`${API_BASE}/players?search=${encodeURIComponent(token)}`)
        .then((res) => res.json())
        .then((data) => setSuggestions((data && data.matches) || []))
        .catch(() => setSuggestions([]));
    }, 250);

    return () => clearTimeout(debounceRef.current);
  }, [value, disabled]);

  function pick(name) {
    const words = value.split(/\s+/);
    const replaceCount = Math.min(2, value.trim().split(/\s+/).length);
    words.splice(Math.max(0, words.length - replaceCount), replaceCount, name);
    setValue(words.join(" ") + " ");
    setSuggestions([]);
  }

  function submit(e) {
    e.preventDefault();
    if (!value.trim() || disabled) return;
    onAsk(value);
    setValue("");
    setSuggestions([]);
  }

  return (
    <form className="input-bar" onSubmit={submit}>
      <div className="input-bar-wrap">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Ask about any match, player, or number since 2002..."
          disabled={disabled}
          aria-label="Ask a cricket question"
          autoComplete="off"
          onBlur={() => {
            setTimeout(() => setSuggestions([]), 150);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setSuggestions([]);
          }}
        />
        {suggestions.length > 0 && (
          <ul className="autocomplete-list" role="listbox">
            {suggestions.map((name) => (
              <li
                key={name}
                role="option"
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(name);
                }}
              >
                {name}
              </li>
            ))}
          </ul>
        )}
      </div>
      {disabled ? (
        <button type="button" className="input-bar-stop" onClick={onCancel}>
          Stop
        </button>
      ) : (
        <button type="submit" disabled={disabled || !value.trim()}>
          Ask
        </button>
      )}
    </form>
  );
}
