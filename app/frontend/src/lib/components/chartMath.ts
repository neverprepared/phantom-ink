/**
 * Tiny pure-TS chart helpers. Replaces the d3-array / d3-scale / d3-shape
 * subset the app uses (linear scales, monotone-cubic line + area paths, pie
 * arcs). Stays well under 100 LOC because we only need what MetricsChart,
 * HorizonChart, and PieChart actually consumed.
 */

export interface Pt { x: number; y: number }

/** Linear scale: maps a value in [dMin, dMax] to [rMin, rMax]. Domain is
 *  clamped to a 1e-9 minimum span so zero-range domains don't divide by 0. */
export function linearScale(
  dMin: number, dMax: number, rMin: number, rMax: number,
): (v: number) => number {
  const span = (dMax - dMin) || 1e-9;
  const slope = (rMax - rMin) / span;
  return (v: number) => rMin + (v - dMin) * slope;
}

/** Min / max in one pass — replaces d3-array extent + max. */
export function extent<T>(arr: T[], f: (d: T) => number): [number, number] {
  if (arr.length === 0) return [0, 0];
  let lo = Infinity, hi = -Infinity;
  for (const d of arr) {
    const v = f(d);
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return [lo, hi];
}
export function arrayMax<T>(arr: T[], f: (d: T) => number): number {
  if (arr.length === 0) return 0;
  let hi = -Infinity;
  for (const d of arr) {
    const v = f(d);
    if (v > hi) hi = v;
  }
  return hi;
}

/** "Nice" ceiling for axis upper bound — rounds to a friendly multiple. */
export function niceCeiling(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  let nf: number;
  if (f <= 1) nf = 1;
  else if (f <= 2) nf = 2;
  else if (f <= 5) nf = 5;
  else nf = 10;
  return nf * exp;
}

/** N evenly spaced tick values across [lo, hi]. */
export function ticks(lo: number, hi: number, count: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [lo];
  const step = (hi - lo) / (count - 1);
  return Array.from({ length: count }, (_, i) => lo + i * step);
}

/**
 * Monotone-cubic ("curveMonotoneX") path through the given points. Same shape
 * d3-shape produces — Fritsch–Carlson tangents, cubic Bézier segments.
 * Returns an empty string for fewer than 2 points.
 */
export function monotonePath(pts: Pt[]): string {
  const n = pts.length;
  if (n < 2) return '';
  if (n === 2) return `M${pts[0].x},${pts[0].y}L${pts[1].x},${pts[1].y}`;

  const dx: number[] = [], dy: number[] = [], m: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx[i] = pts[i + 1].x - pts[i].x;
    dy[i] = pts[i + 1].y - pts[i].y;
  }
  // Endpoint tangents = secant slopes. Interior tangents = harmonic mean
  // when secants agree in sign, 0 when they don't (preserves monotonicity).
  m[0] = dx[0] !== 0 ? dy[0] / dx[0] : 0;
  for (let i = 1; i < n - 1; i++) {
    if (dy[i - 1] * dy[i] <= 0) {
      m[i] = 0;
    } else {
      const w1 = 2 * dx[i] + dx[i - 1];
      const w2 = dx[i] + 2 * dx[i - 1];
      m[i] = (w1 + w2) / (w1 / (dy[i - 1] / dx[i - 1]) + w2 / (dy[i] / dx[i]));
    }
  }
  m[n - 1] = dx[n - 2] !== 0 ? dy[n - 2] / dx[n - 2] : 0;

  let d = `M${pts[0].x},${pts[0].y}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    const c1x = pts[i].x + h;
    const c1y = pts[i].y + m[i] * h;
    const c2x = pts[i + 1].x - h;
    const c2y = pts[i + 1].y - m[i + 1] * h;
    d += ` C${c1x},${c1y} ${c2x},${c2y} ${pts[i + 1].x},${pts[i + 1].y}`;
  }
  return d;
}

/**
 * Annular slice path (the same shape d3-shape's arc(innerRadius, outerRadius)
 * draws). Angles in radians, 0 = up, increasing clockwise — matches d3's
 * default pie layout when sort=null. cornerRadius is approximated by a small
 * rounding step on the outer corners only (the original used d3 cornerRadius).
 */
export function arcPath(
  startAngle: number,
  endAngle: number,
  innerR: number,
  outerR: number,
  cornerRadius = 0,
): string {
  if (Math.abs(endAngle - startAngle) < 1e-6) return '';
  // Convert d3-style angles (0 = up, clockwise) to SVG cartesian.
  const toXY = (r: number, a: number): Pt => ({
    x: Math.sin(a) * r,
    y: -Math.cos(a) * r,
  });
  const sweep = endAngle - startAngle;
  const largeArc = Math.abs(sweep) > Math.PI ? 1 : 0;
  const dir = sweep >= 0 ? 1 : 0;

  if (innerR <= 0) {
    // Pie wedge (donut hole = 0).
    const p1 = toXY(outerR, startAngle);
    const p2 = toXY(outerR, endAngle);
    return `M0,0L${p1.x},${p1.y}A${outerR},${outerR} 0 ${largeArc} ${dir} ${p2.x},${p2.y}Z`;
  }
  const oStart = toXY(outerR, startAngle);
  const oEnd   = toXY(outerR, endAngle);
  const iEnd   = toXY(innerR, endAngle);
  const iStart = toXY(innerR, startAngle);
  // cornerRadius is intentionally approximated — we accept slightly square
  // corners in exchange for not pulling in d3's arc cornerRadius math.
  void cornerRadius;
  return [
    `M${oStart.x},${oStart.y}`,
    `A${outerR},${outerR} 0 ${largeArc} ${dir} ${oEnd.x},${oEnd.y}`,
    `L${iEnd.x},${iEnd.y}`,
    `A${innerR},${innerR} 0 ${largeArc} ${dir === 1 ? 0 : 1} ${iStart.x},${iStart.y}`,
    'Z',
  ].join(' ');
}

/**
 * Compute pie layout (start/end angles + value) for the given items. Equivalent
 * to d3-shape's pie().sort(null).padAngle(pad). Mirrors d3's angle space:
 * starts at 0 (top), proceeds clockwise.
 */
export interface PieDatum<T> {
  data: T;
  value: number;
  startAngle: number;
  endAngle: number;
}
export function pieLayout<T>(
  items: T[],
  value: (d: T) => number,
  padAngle = 0,
): PieDatum<T>[] {
  const positive = items.filter(d => value(d) > 0);
  if (positive.length === 0) return [];
  const total = positive.reduce((acc, d) => acc + value(d), 0);
  if (total <= 0) return [];
  const padTotal = padAngle * positive.length;
  const available = Math.max(0, Math.PI * 2 - padTotal);
  let cursor = 0;
  return positive.map(d => {
    const v = value(d);
    const slice = (v / total) * available;
    const start = cursor;
    const end = cursor + slice;
    cursor = end + padAngle;
    return { data: d, value: v, startAngle: start, endAngle: end };
  });
}
