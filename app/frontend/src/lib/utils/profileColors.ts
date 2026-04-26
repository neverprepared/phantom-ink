/** Per-profile color assignment: hash-based auto-color with optional override. */

export interface ProfileColor {
  text: string;
  bg: string;
  border: string;
  index: number;
}

/** 10 visually distinct mid-tone colors that work on dark + light themes. */
export const PROFILE_PALETTE: ProfileColor[] = [
  { text: '#60a5fa', bg: 'rgba(96,165,250,0.10)',  border: 'rgba(96,165,250,0.20)',  index: 0 },  // blue
  { text: '#a78bfa', bg: 'rgba(167,139,250,0.10)', border: 'rgba(167,139,250,0.20)', index: 1 },  // purple
  { text: '#fb923c', bg: 'rgba(251,146,60,0.10)',  border: 'rgba(251,146,60,0.20)',  index: 2 },  // orange
  { text: '#fbbf24', bg: 'rgba(251,191,36,0.10)',  border: 'rgba(251,191,36,0.20)',  index: 3 },  // amber
  { text: '#4ade80', bg: 'rgba(74,222,128,0.10)',  border: 'rgba(74,222,128,0.20)',  index: 4 },  // green
  { text: '#f472b6', bg: 'rgba(244,114,182,0.10)', border: 'rgba(244,114,182,0.20)', index: 5 },  // pink
  { text: '#2dd4bf', bg: 'rgba(45,212,191,0.10)',  border: 'rgba(45,212,191,0.20)',  index: 6 },  // teal
  { text: '#fb7185', bg: 'rgba(251,113,133,0.10)', border: 'rgba(251,113,133,0.20)', index: 7 },  // rose
  { text: '#818cf8', bg: 'rgba(129,140,248,0.10)', border: 'rgba(129,140,248,0.20)', index: 8 },  // indigo
  { text: '#facc15', bg: 'rgba(250,204,21,0.10)',  border: 'rgba(250,204,21,0.20)',  index: 9 },  // yellow
];

/** djb2 hash → palette index. Deterministic for any string. */
function hashProfileName(name: string): number {
  let hash = 5381;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) + hash + name.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % PROFILE_PALETTE.length;
}

/** Resolve a profile name to its color (override takes precedence over hash). */
export function getProfileColor(name: string, override?: string | null): ProfileColor {
  if (override != null && override !== '') {
    const idx = parseInt(override, 10);
    if (idx >= 0 && idx < PROFILE_PALETTE.length) {
      return PROFILE_PALETTE[idx];
    }
  }
  return PROFILE_PALETTE[hashProfileName(name)];
}

/** Returns an inline CSS string for color, background, and border-color. */
export function profileColorStyle(color: ProfileColor): string {
  return `color: ${color.text}; background: ${color.bg}; border-color: ${color.border};`;
}
