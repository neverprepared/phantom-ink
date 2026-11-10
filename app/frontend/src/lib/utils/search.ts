/**
 * Tokenized substring search over rows already in memory.
 *
 * This is deliberately NOT a full-text index. The lists it serves (the Code
 * panel's needs-attention / notifications / repositories) are hundreds of rows
 * that have already been fetched, so building and invalidating an index would
 * cost more than scanning. If a caller ever has thousands of rows, that is the
 * point to reach for a real index, not before.
 *
 * The matching rule is tokenized AND: every whitespace-separated term must
 * appear as a case-insensitive substring in SOME field of the row. That is what
 * makes `phan rout` find `phantom-router` and `router 443` find PR #443 in it —
 * a contiguous match over one field finds neither.
 */

/** Lowercased, whitespace-split terms. A blank query yields no terms. */
export function tokenize(query: string): string[] {
  return (query ?? '')
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

/**
 * True when every term appears in at least one field. No terms matches
 * everything, so callers can pass an empty query through unguarded.
 *
 * Fields are joined with a newline rather than concatenated: a term must not be
 * able to match by straddling the boundary between two unrelated fields.
 */
export function matchesAll(terms: string[], fields: (string | undefined)[]): boolean {
  if (terms.length === 0) return true;
  const hay = fields.filter(Boolean).join('\n').toLowerCase();
  return terms.every((t) => hay.includes(t));
}

/** One run of text, flagged as matched or not. */
export interface Segment {
  text: string;
  match: boolean;
}

/**
 * Split `text` into alternating matched/unmatched runs for the given terms,
 * preserving the source casing. Ranges from different terms that overlap or
 * touch are merged, so `['pha','ant']` over "phantom" yields one `phant` run
 * rather than two abutting marks.
 */
export function highlightSegments(text: string, terms: string[]): Segment[] {
  const src = text ?? '';
  if (!src) return [];
  if (terms.length === 0) return [{ text: src, match: false }];

  const lower = src.toLowerCase();
  const ranges: [number, number][] = [];
  for (const term of terms) {
    if (!term) continue;
    for (let i = lower.indexOf(term); i !== -1; i = lower.indexOf(term, i + 1)) {
      ranges.push([i, i + term.length]);
    }
  }
  if (ranges.length === 0) return [{ text: src, match: false }];

  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: [number, number][] = [ranges[0]];
  for (const [start, end] of ranges.slice(1)) {
    const last = merged[merged.length - 1];
    if (start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }

  const out: Segment[] = [];
  let cursor = 0;
  for (const [start, end] of merged) {
    if (start > cursor) out.push({ text: src.slice(cursor, start), match: false });
    out.push({ text: src.slice(start, end), match: true });
    cursor = end;
  }
  if (cursor < src.length) out.push({ text: src.slice(cursor), match: false });
  return out;
}
