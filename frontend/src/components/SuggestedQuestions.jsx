const SUGGESTIONS = [
  "Who has scored the most runs in the Indian Premier League?",
  "Compare Virat Kohli's strike rate in the IPL vs T20 internationals",
  "Which team has won the most matches at Eden Gardens?",
  "Who has taken the most wickets in T20 World Cups?",
];

export default function SuggestedQuestions({ onPick }) {
  return (
    <div className="suggestions">
      <p className="suggestions-label">try asking</p>
      <div className="suggestions-grid">
        {SUGGESTIONS.map((q) => (
          <button key={q} className="suggestion-chip" onClick={() => onPick(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
