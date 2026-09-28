import { useId } from "react";

// A cartoon red leather cricket ball: glossy red body, dark outline, a
// raised seam with two rows of cream stitching, and (optionally) motion
// swooshes trailing on the left. Transparent background. The same drawing
// is saved as public/favicon.svg -- keep the two in step.
export default function CricketBall({ size = "1em", motion = false, className, title }) {
  const uid = useId().replace(/:/g, "");
  const body = `ball-body-${uid}`;
  const clip = `ball-clip-${uid}`;
  return (
    <svg
      className={className}
      width={motion ? `calc(${size} * 1.2)` : size}
      height={size}
      viewBox={motion ? "-9 0 77 64" : "0 0 64 64"}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      xmlns="http://www.w3.org/2000/svg"
    >
      {title && <title>{title}</title>}
      <defs>
        <radialGradient id={body} cx="38%" cy="32%" r="72%">
          <stop offset="0" stopColor="#ff5c47" />
          <stop offset="0.45" stopColor="#e0221c" />
          <stop offset="1" stopColor="#8f0d0b" />
        </radialGradient>
        <clipPath id={clip}>
          <circle cx="32" cy="32" r="26" />
        </clipPath>
      </defs>

      {/* Cream swooshes: the app is dark, so the reference's dark lines would vanish. */}
      {motion && (
        <g fill="none" stroke="#f1e8d6" strokeOpacity="0.7" strokeLinecap="round">
          <path d="M 7.4 14.8 A 30 30 0 0 0 9 51.3" strokeWidth="2.4" />
          <path d="M 0.1 20.4 A 34 34 0 0 0 2.6 49" strokeWidth="2" />
          <path d="M -5.4 25.4 A 38 38 0 0 0 -3.7 45" strokeWidth="1.6" />
        </g>
      )}

      <circle cx="32" cy="32" r="26" fill={`url(#${body})`} />

      <g clipPath={`url(#${clip})`} fill="none" strokeLinecap="round">
        {/* seam band */}
        <path d="M 33 3 C 48 19, 48 44, 31 62" stroke="#9e1410" strokeWidth="10" />
        <path d="M 33 3 C 48 19, 48 44, 31 62" stroke="#5a0b08" strokeWidth="1.2" />
        {/* two rows of stitching: dark outline under cream stitches */}
        <path d="M 28.4 3 C 43.4 19, 43.4 44, 26.4 62" stroke="#4a0906" strokeWidth="3.8" strokeDasharray="2.2 2.4" />
        <path d="M 28.4 3 C 43.4 19, 43.4 44, 26.4 62" stroke="#f6e7c7" strokeWidth="2.5" strokeDasharray="2.2 2.4" />
        <path d="M 37.6 3 C 52.6 19, 52.6 44, 35.6 62" stroke="#4a0906" strokeWidth="3.8" strokeDasharray="2.2 2.4" />
        <path d="M 37.6 3 C 52.6 19, 52.6 44, 35.6 62" stroke="#f6e7c7" strokeWidth="2.5" strokeDasharray="2.2 2.4" />
        {/* gloss */}
        <ellipse cx="22" cy="18.5" rx="8" ry="4.6" transform="rotate(-32 22 18.5)" fill="#ffffff" fillOpacity="0.5" stroke="none" />
        <path d="M 12.5 30 C 13.5 24, 16 20.5, 19 18" stroke="#ffffff" strokeOpacity="0.35" strokeWidth="1.6" />
      </g>

      <circle cx="32" cy="32" r="26" fill="none" stroke="#2a0805" strokeWidth="2.4" />
    </svg>
  );
}
