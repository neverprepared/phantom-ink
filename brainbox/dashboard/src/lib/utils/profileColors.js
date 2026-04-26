/** Per-profile color assignment: deterministic hash-based palette.
 *  Mirrors app/frontend/src/lib/utils/profileColors.ts so colors match. */

export const PROFILE_PALETTE = [
  { text: '#60a5fa', bg: 'rgba(96,165,250,0.10)',  border: 'rgba(96,165,250,0.20)'  },  // blue
  { text: '#a78bfa', bg: 'rgba(167,139,250,0.10)', border: 'rgba(167,139,250,0.20)' },  // purple
  { text: '#fb923c', bg: 'rgba(251,146,60,0.10)',  border: 'rgba(251,146,60,0.20)'  },  // orange
  { text: '#fbbf24', bg: 'rgba(251,191,36,0.10)',  border: 'rgba(251,191,36,0.20)'  },  // amber
  { text: '#4ade80', bg: 'rgba(74,222,128,0.10)',  border: 'rgba(74,222,128,0.20)'  },  // green
  { text: '#f472b6', bg: 'rgba(244,114,182,0.10)', border: 'rgba(244,114,182,0.20)' },  // pink
  { text: '#2dd4bf', bg: 'rgba(45,212,191,0.10)',  border: 'rgba(45,212,191,0.20)'  },  // teal
  { text: '#fb7185', bg: 'rgba(251,113,133,0.10)', border: 'rgba(251,113,133,0.20)' },  // rose
  { text: '#818cf8', bg: 'rgba(129,140,248,0.10)', border: 'rgba(129,140,248,0.20)' },  // indigo
  { text: '#facc15', bg: 'rgba(250,204,21,0.10)',  border: 'rgba(250,204,21,0.20)'  },  // yellow
];

/** djb2 hash → palette index. Deterministic for any string. */
function hashProfileName(name) {
  let hash = 5381;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) + hash + name.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % PROFILE_PALETTE.length;
}

/** Resolve a profile name to its color. */
export function getProfileColor(name) {
  return PROFILE_PALETTE[hashProfileName(name)];
}

/** Returns an inline style string for color + background. */
export function profileColorStyle(name) {
  const c = getProfileColor(name);
  return `color: ${c.text}; background: ${c.bg};`;
}
