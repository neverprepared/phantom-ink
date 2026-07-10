/**
 * Shared display formatters. Byte ladders, relative-time, and clock formatting
 * were reimplemented ~20× across panels with divergent thresholds/units; this
 * is the single source of truth. Pure functions (the `now` params make the
 * time helpers testable). Callers own their empty sentinel where it differs —
 * these return a canonical value ('0 B', '' etc.), wrap at the call site for
 * '–'/'never' variants.
 */

/** Format a byte count as B / KB / MB / GB (base 1024). 0 or invalid → "0 B". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

/** Format a memory size already expressed in MiB as MiB / GiB. */
export function formatMem(mib: number): string {
  if (!Number.isFinite(mib) || mib <= 0) return '0 MiB';
  return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GiB` : `${Math.round(mib)} MiB`;
}

/**
 * Relative time from an epoch-ms timestamp: "just now" (<5s), then Ns / Nm /
 * Nh / Nd ago. Falsy timestamp → "". Pass `now` for deterministic tests.
 */
export function timeAgo(tsMs: number, now: number = Date.now()): string {
  if (!tsMs) return '';
  const diff = now - tsMs;
  if (diff < 5_000) return 'just now';
  if (diff < 60_000) return `${Math.floor(diff / 1_000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/**
 * Relative time for recent timestamps, falling back to an absolute short date
 * (e.g. "Jul 5") past 24h. Falsy timestamp → `empty` (default "never"). Used
 * for last-run/last-triggered columns.
 */
export function timeAgoOrDate(
  tsMs: number | null | undefined,
  empty = 'never',
  now: number = Date.now(),
): string {
  if (!tsMs) return empty;
  const diff = now - tsMs;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(tsMs).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

/** 24-hour clock (HH:MM, or HH:MM:SS with { seconds: true }) from epoch ms. */
export function formatClock(ms: number, opts: { seconds?: boolean } = {}): string {
  return new Date(ms).toLocaleTimeString(undefined, {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    ...(opts.seconds ? { second: '2-digit' } : {}),
  });
}
