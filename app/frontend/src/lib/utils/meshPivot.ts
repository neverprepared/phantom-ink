// Group the flat mesh peer list into a vault → profile → machine tree.
//
// The daemon reports one peer per (machine, profile, vault) triple, so a node
// syncing 3 profiles × 4 vaults against 2 machines is 24 flat rows. What an
// operator actually asks is "how is memory doing?", then "which profile?",
// then "which machine?" — so the table roots on the vault, nests the profile
// under it, and puts each machine's latency and errors on its own line with
// the full <machine>-<profile>-<vault> id beside them.
//
// Kept out of the Svelte component so it stays a pure data transform: the panel
// renders what these functions return and holds no parsing logic of its own.

/** One peer as the daemon reports it (the fields this grouping needs). */
export interface PivotPeer {
  id: string;
  base_url: string;
  profile: string;
  live: boolean;
  live_note?: string;
  db_lag: string;
  links_lag: string;
  cursor_age: string;
  errors_24h?: number;
  last_error?: { at: string; phase: string; class: string; err: string };
}

/** A single machine's peer for one (profile, vault). */
export interface MachineEntry {
  machine: string;
  peer: PivotPeer;
}

/** One profile within a vault, across every machine serving it. */
export interface ProfileGroup {
  key: string; // "<vault>/<profile>"
  profile: string;
  machines: MachineEntry[];
  liveCount: number;
  peerCount: number;
  worstLag: string;
  errors24h: number;
}

/** One vault at the root of the table, across every profile and machine. */
export interface VaultGroup {
  key: string; // the vault name, unique at root
  vault: string;
  profiles: ProfileGroup[];
  liveCount: number;
  peerCount: number;
  worstLag: string;
  errors24h: number;
}

/**
 * Split a peer id into its machine and vault parts.
 *
 * Ids are `<machine>-<profile>-<vault>`, and the machine name may itself
 * contain hyphens (`m3-64`, `m5-128`), so splitting on the first hyphen is
 * wrong. The profile arrives as its own authoritative field, so anchor on it:
 * the vault is the final segment, and the machine is whatever precedes
 * `-<profile>-<vault>`.
 *
 * Falls back to the leading segment when the id does not have that shape, so a
 * malformed or renamed peer still lands somewhere visible instead of vanishing.
 */
export function splitPeerID(id: string, profile: string): { machine: string; vault: string } {
  const lastDash = id.lastIndexOf('-');
  if (lastDash > 0) {
    const vault = id.slice(lastDash + 1);
    const head = id.slice(0, lastDash); // "<machine>-<profile>"
    const suffix = `-${profile}`;
    if (head.endsWith(suffix) && head.length > suffix.length) {
      return { machine: head.slice(0, -suffix.length), vault };
    }
  }
  const firstDash = id.indexOf('-');
  return {
    machine: firstDash > 0 ? id.slice(0, firstDash) : id,
    vault: lastDash > 0 ? id.slice(lastDash + 1) : '',
  };
}

// Lag strings are daemon-formatted and mix numbers with status labels:
// "0", ">=100", "off", "err", "n/a", "never", "-", "proto!", "fenced".
// Ranking them lets a group summarise its worst member. A status that means
// "sync is not working" outranks any number, because an operator needs to see
// "err" ahead of a large-but-progressing backlog. Benign/disabled labels rank
// below zero so they never win a summary over a real number.
const LAG_RANK_FLOOR: Record<string, number> = { off: -3, 'n/a': -2, '-': -1 };
const LAG_RANK_ALARM: Record<string, number> = { fenced: 1e6, 'proto!': 1e7, err: 1e8 };

function lagRank(lag: string): number {
  if (lag in LAG_RANK_ALARM) return LAG_RANK_ALARM[lag];
  if (lag in LAG_RANK_FLOOR) return LAG_RANK_FLOOR[lag];
  // ">=100" means "at least 100 behind" — rank it just above a plain 100.
  const atLeast = lag.startsWith('>=') ? Number(lag.slice(2)) : NaN;
  if (Number.isFinite(atLeast)) return atLeast + 0.5;
  const n = Number(lag);
  return Number.isFinite(n) ? n : 0;
}

/** The worst of a set of lag strings, by the ranking above. Empty ⇒ "-". */
export function worstLag(lags: string[]): string {
  if (lags.length === 0) return '-';
  return lags.reduce((worst, l) => (lagRank(l) > lagRank(worst) ? l : worst));
}

/**
 * Build the vault → profile → machine tree, sorted at every level so the table
 * reads in a stable order regardless of the daemon's peer ordering.
 *
 * Each group carries its own live/peer counts, worst lag across both its db and
 * links streams, and summed recent errors, so a collapsed row still says
 * whether anything beneath it needs attention.
 */
export function buildVaultTree(peers: PivotPeer[]): VaultGroup[] {
  const vaults = new Map<string, Map<string, MachineEntry[]>>();
  for (const peer of peers) {
    const { machine, vault } = splitPeerID(peer.id, peer.profile);
    let byProfile = vaults.get(vault);
    if (!byProfile) {
      byProfile = new Map();
      vaults.set(vault, byProfile);
    }
    const entries = byProfile.get(peer.profile) ?? [];
    entries.push({ machine, peer });
    byProfile.set(peer.profile, entries);
  }

  const out: VaultGroup[] = [];
  for (const [vault, byProfile] of vaults) {
    const profiles: ProfileGroup[] = [];
    for (const [profile, entries] of byProfile) {
      entries.sort((a, b) => a.machine.localeCompare(b.machine));
      profiles.push({
        key: `${vault}/${profile}`,
        profile,
        machines: entries,
        liveCount: entries.filter((e) => e.peer.live).length,
        peerCount: entries.length,
        worstLag: worstLag(entries.flatMap((e) => [e.peer.db_lag, e.peer.links_lag])),
        errors24h: entries.reduce((n, e) => n + (e.peer.errors_24h ?? 0), 0),
      });
    }
    profiles.sort((a, b) => a.profile.localeCompare(b.profile));
    out.push({
      key: vault,
      vault,
      profiles,
      liveCount: profiles.reduce((n, p) => n + p.liveCount, 0),
      peerCount: profiles.reduce((n, p) => n + p.peerCount, 0),
      worstLag: worstLag(profiles.map((p) => p.worstLag)),
      errors24h: profiles.reduce((n, p) => n + p.errors24h, 0),
    });
  }
  return out.sort((a, b) => a.vault.localeCompare(b.vault));
}
