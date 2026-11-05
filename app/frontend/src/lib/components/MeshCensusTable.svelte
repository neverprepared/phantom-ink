<script lang="ts">
  // "What do I hold, and do my nodes agree?"
  //
  // Every node in the mesh keeps a FULL replica, so there is no mesh-wide total
  // to show — summing the columns would multiply the same records by the number
  // of nodes. The headline figure is the local node's count; the per-node
  // columns exist to be COMPARED, and a disagreement is the signal.
  //
  // A node that did not answer renders an em dash, never 0: a zero there is
  // indistinguishable from "this node lost every record".

  export interface CountCell {
    node_id: string;
    known: boolean;
    live: number;
    tombstoned: number;
  }
  export interface CountRow {
    profile: string;
    vault: string;
    live: number;
    diverged: boolean;
    cells: CountCell[];
  }
  export interface Census {
    local_node_id: string;
    nodes: string[];
    rows: CountRow[];
    unreachable?: string[];
  }

  let { census, profile = null }: { census: Census | null; profile?: string | null } = $props();

  // Scope to the active profile when one is given — the app is profile-scoped
  // throughout, and showing three profiles at once buries the four numbers the
  // operator came for. Falls back to everything when no profile is set.
  const rows = $derived(
    (census?.rows ?? []).filter((r) => !profile || r.profile === profile),
  );
  const divergedCount = $derived(rows.filter((r) => r.diverged).length);
  const nodes = $derived(census?.nodes ?? []);
  const unreachable = $derived(census?.unreachable ?? []);
</script>

{#if census && rows.length}
  <section class="card census">
    <h2>
      Records
      {#if divergedCount > 0}
        <span class="verdict bad" title="these nodes do not hold the same records">
          {divergedCount} diverged
        </span>
      {:else if unreachable.length}
        <span class="verdict warn" title="cannot compare every node">
          {unreachable.length} unreachable
        </span>
      {:else}
        <span class="verdict ok" title="every node reports identical counts">in sync</span>
      {/if}
    </h2>

    <table>
      <thead>
        <tr>
          <th class="vault-col">vault</th>
          <th class="total-col" title="count on this node — not a mesh-wide sum">this node</th>
          {#each nodes as n (n)}
            <th class="node-col" class:is-local={n === census.local_node_id} title={n}>
              {n === census.local_node_id ? `${n} (local)` : n}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each rows as r (r.profile + '/' + r.vault)}
          <tr class:diverged={r.diverged}>
            <td class="vault-col">
              {r.vault}
              {#if !profile}<span class="prof">{r.profile}</span>{/if}
            </td>
            <td class="total-col"><strong>{r.live.toLocaleString()}</strong></td>
            {#each r.cells as c (c.node_id)}
              <td class="node-col" class:unknown={!c.known}>
                {#if c.known}
                  {c.live.toLocaleString()}
                  {#if c.tombstoned > 0}
                    <span class="tomb" title="{c.tombstoned.toLocaleString()} forgotten (tombstoned) records">
                      +{c.tombstoned.toLocaleString()}
                    </span>
                  {/if}
                {:else}
                  <span class="dash" title="node did not answer — count unknown">—</span>
                {/if}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>

    <p class="foot">
      Live records per node, with <span class="tomb">+n</span> forgotten alongside.
      Each node holds a full replica, so these columns should match — they are not added up.
      {#if unreachable.length}
        Unreachable: {unreachable.join(', ')}.
      {/if}
    </p>
  </section>
{/if}

<style>
  .census table {
    width: 100%;
    border-collapse: collapse;
    font-variant-numeric: tabular-nums;
  }
  .census th,
  .census td {
    padding: 0.35rem 0.6rem;
    text-align: right;
    border-bottom: 1px solid var(--border, #e5e5e5);
    white-space: nowrap;
  }
  .census th {
    font-weight: 600;
    font-size: 0.8rem;
    opacity: 0.75;
  }
  .vault-col {
    text-align: left !important;
  }
  .total-col {
    border-right: 1px solid var(--border, #e5e5e5);
  }
  .node-col.is-local {
    font-weight: 700;
  }
  .prof {
    opacity: 0.55;
    font-size: 0.78rem;
    margin-left: 0.4rem;
  }
  .tomb {
    opacity: 0.55;
    font-size: 0.78rem;
    margin-left: 0.25rem;
  }
  .dash {
    opacity: 0.4;
  }
  .node-col.unknown {
    background: color-mix(in srgb, currentColor 4%, transparent);
  }
  tr.diverged td {
    background: color-mix(in srgb, #d9822b 14%, transparent);
  }
  .verdict {
    margin-left: 0.6rem;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.1rem 0.45rem;
    border-radius: 999px;
    vertical-align: middle;
  }
  .verdict.ok {
    background: color-mix(in srgb, #2d9d78 18%, transparent);
  }
  .verdict.warn {
    background: color-mix(in srgb, #d9a22b 20%, transparent);
  }
  .verdict.bad {
    background: color-mix(in srgb, #d9822b 22%, transparent);
  }
  .foot {
    margin: 0.5rem 0 0;
    font-size: 0.78rem;
    opacity: 0.65;
    line-height: 1.4;
  }
</style>
