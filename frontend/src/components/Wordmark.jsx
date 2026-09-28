import CricketBall from "./CricketBall.jsx";

// "Doosra" with the first "o" drawn as a cricket ball. Screen readers get
// the plain word.
export default function Wordmark({ small = false }) {
  return (
    <span className={`masthead-mark${small ? " small" : ""}`} aria-label="Doosra" role="img">
      <span aria-hidden="true">
        D<CricketBall className="wordmark-ball" size="0.66em" />osra
      </span>
    </span>
  );
}
