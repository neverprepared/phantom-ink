<script lang="ts">
  // Memory Graph — embeds the phantom-mindwalk 3D memory-graph visualizer
  // (managed as the "mindwalk" docker integration). The container is launched
  // by the Go backend (StartService), which injects the ACTIVE profile's
  // memory-vault token, a container-reachable BRAIN_URL, and the build-context
  // path. Because that token is baked at `compose up`, switching the active
  // profile requires recreating the container — this panel does that (stop +
  // start) and reloads the iframe. Same embed pattern as SessionsPanel.
  import { onMount, onDestroy } from 'svelte';
  import { getApi, openInBrowser } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import Spinner from '../components/Spinner.svelte';

  const SERVICE = 'mindwalk';
  const GRAPH_URL = 'http://localhost:9997';

  let running = $state(false);
  let busy = $state(false);
  let busyLabel = $state('');
  let loaded = $state(false); // first status check done
  let reloadToken = $state(0); // bump to force the iframe to remount
  let poll: ReturnType<typeof setInterval> | null = null;
  let lastProfile = $state<string | null>(null);

  async function status(): Promise<boolean> {
    const a = await getApi();
    if (!a) return false;
    const svcs = (await a.ListServices()) ?? [];
    const mw = svcs.find((s: any) => s.name === SERVICE);
    return !!mw?.running;
  }

  async function refresh() {
    running = await status();
    loaded = true;
  }

  async function start() {
    busy = true;
    busyLabel = 'Starting (first run builds the image — this can take a minute)…';
    const a = await getApi();
    try {
      await a?.StartService(SERVICE);
      // Container may still be booting; poll briefly until it answers.
      for (let i = 0; i < 20 && !(await status()); i++) {
        await sleep(1000);
      }
      running = await status();
      if (running) {
        reloadToken++;
        notifications.success('Memory Graph started');
      } else {
        notifications.error('Memory Graph did not come up — check docker logs for phantom-mindwalk');
      }
    } catch (err: any) {
      notifications.error(`Failed to start Memory Graph: ${err}`);
    } finally {
      busy = false;
      busyLabel = '';
    }
  }

  async function stop() {
    busy = true;
    busyLabel = 'Stopping…';
    const a = await getApi();
    try {
      await a?.StopService(SERVICE);
      running = false;
    } catch (err: any) {
      notifications.error(`Failed to stop Memory Graph: ${err}`);
    } finally {
      busy = false;
      busyLabel = '';
    }
  }

  // Rebind to the active profile: the running container is pinned to whichever
  // profile's token it started with, so recreate it on a profile switch.
  async function rebind() {
    busy = true;
    busyLabel = 'Rebinding to the active profile…';
    const a = await getApi();
    try {
      await a?.StopService(SERVICE);
      await a?.StartService(SERVICE);
      for (let i = 0; i < 20 && !(await status()); i++) {
        await sleep(1000);
      }
      running = await status();
      if (running) reloadToken++;
    } catch (err: any) {
      notifications.error(`Failed to rebind Memory Graph: ${err}`);
    } finally {
      busy = false;
      busyLabel = '';
    }
  }

  function sleep(ms: number) {
    return new Promise((r) => setTimeout(r, ms));
  }

  onMount(async () => {
    lastProfile = profileState.active?.name ?? null;
    await refresh();
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
  });

  // React to active-profile changes; recreate the container if it is running.
  $effect(() => {
    const current = profileState.active?.name ?? null;
    if (loaded && current !== lastProfile) {
      lastProfile = current;
      if (running && !busy) rebind();
    }
  });
</script>

<div class="memory-graph">
  <header class="bar">
    <div class="titles">
      <h2>Memory Graph</h2>
      <span class="sub">
        phantom-mindwalk · {profileState.active?.name ?? 'no profile'} · memory vault
      </span>
    </div>
    <div class="actions">
      {#if busy}
        <span class="busy"><Spinner /> {busyLabel}</span>
      {/if}
      {#if running}
        <button class="btn" onclick={() => reloadToken++} disabled={busy} title="Reload the graph">Reload</button>
        <button class="btn" onclick={() => openInBrowser(GRAPH_URL)} disabled={busy}>Open in browser</button>
        <button class="btn danger" onclick={stop} disabled={busy}>Stop</button>
      {:else}
        <button class="btn primary" onclick={start} disabled={busy}>Start</button>
      {/if}
    </div>
  </header>

  <div class="body">
    {#if !loaded}
      <div class="center"><Spinner /></div>
    {:else if running}
      {#key reloadToken}
        <iframe class="graph-frame" src={GRAPH_URL} title="phantom-mindwalk memory graph"></iframe>
      {/key}
    {:else}
      <div class="center empty">
        <p class="lead">The memory-graph visualizer is not running.</p>
        <p class="hint">
          Start it to see a 3D force-directed view of the active profile's
          phantom-brain memory — nodes are records, edges are semantic
          similarity and supersedes links.
        </p>
        {#if busy}
          <p class="hint">{busyLabel}</p>
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .memory-graph {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }

  .bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--color-border);
    flex-shrink: 0;
  }

  .titles {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }

  .titles h2 {
    margin: 0;
    font-size: 0.95rem;
  }

  .sub {
    font-size: 0.72rem;
    color: var(--color-text-tertiary);
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .busy {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.72rem;
    color: var(--color-text-tertiary);
  }

  .btn {
    font-size: 0.78rem;
    padding: 0.3rem 0.7rem;
    border: 1px solid var(--color-border);
    border-radius: 6px;
    background: var(--color-surface);
    color: var(--color-text);
    cursor: pointer;
  }

  .btn:hover:not(:disabled) {
    background: var(--color-surface-hover, var(--color-surface));
  }

  .btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .btn.primary {
    border-color: var(--color-accent, #4f8cff);
    color: var(--color-accent, #4f8cff);
  }

  .btn.danger {
    border-color: var(--color-danger, #d9534f);
    color: var(--color-danger, #d9534f);
  }

  .body {
    flex: 1;
    min-height: 0;
    display: flex;
  }

  .graph-frame {
    flex: 1;
    border: none;
    width: 100%;
    background: #05060a;
  }

  .center {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.6rem;
    padding: 2rem;
    text-align: center;
  }

  .lead {
    margin: 0;
    color: var(--color-text);
    font-size: 0.9rem;
  }

  .hint {
    margin: 0;
    max-width: 30rem;
    color: var(--color-text-tertiary);
    font-size: 0.78rem;
    line-height: 1.4;
  }
</style>
