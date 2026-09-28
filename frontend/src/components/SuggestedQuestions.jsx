const SUGGESTIONS = [
  "Who has scored the most runs in the IPL?",
  "How has Jos Buttler's IPL batting trended season by season?",
  "Who has the best death-overs economy in the IPL?",
  "Compare Babar Azam and Mohammad Rizwan in T20Is",
  "India vs Australia head to head in Tests",
  "Who gets Steve Smith out most in Tests?",
  "Is Chinnaswamy a chasing ground in the IPL?",
  "Most wickets in the Women's Premier League",
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
