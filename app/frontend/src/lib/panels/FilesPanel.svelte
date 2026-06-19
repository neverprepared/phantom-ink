<script lang="ts">
  /**
   * Files panel — MinIO-backed file browser.
   *
   * Two-pane: bucket list on the left (vault | artifacts), folder
   * tree + file grid on the right. Operator can navigate up via the
   * breadcrumb, download via presigned URL, delete via the row action.
   *
   * The whole panel is gated behind GetArtifactsHealth — when MinIO
   * isn't configured or isn't reachable, we render a distinct empty
   * state for each.
   */
  import { onMount } from 'svelte';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  interface Bucket {
    key: string;
    name: string;
    label: string;
  }

  interface Folder {
    name: string;
    prefix: string;
  }

  interface File {
    name: string;
    key: string;
    size: number;
    etag: string;
    last_modified_ms: number;
  }

  let health = $state<{ ok: boolean; reason?: string; endpoint?: string; profile_prefix?: string } | null>(null);
  let buckets = $state<Bucket[]>([]);
  let selectedBucket = $state<Bucket | null>(null);
  let currentPrefix = $state<string>('');
  let folders = $state<Folder[]>([]);
  let files = $state<File[]>([]);
  let loading = $state(false);
  let listingError = $state<string | null>(null);

  async function bootstrap() {
    const api = await getApi();
    if (!api) return;
    try {
      health = await api.GetArtifactsHealth();
      if (health?.ok) {
        buckets = (await api.ListArtifactsBuckets()) ?? [];
        if (buckets.length > 0) {
          await openBucket(buckets[0]);
        }
      }
    } catch (err) {
      health = { ok: false, reason: err instanceof Error ? err.message : String(err) };
    }
  }

  async function openBucket(bucket: Bucket) {
    selectedBucket = bucket;
    currentPrefix = '';
    await refreshListing();
  }

  async function enterFolder(folder: Folder) {
    // The API's `prefix` is namespace-relative — strip the server-side
    // profile prefix the listing returned (it's already in `folder.prefix`).
    const profileRoot = (health?.profile_prefix ?? '').replace(/\/$/, '');
    let rel = folder.prefix;
    if (profileRoot && rel.startsWith(profileRoot + '/')) {
      rel = rel.slice(profileRoot.length + 1);
    }
    currentPrefix = rel;
    await refreshListing();
  }

  async function goUp() {
    if (!currentPrefix) return;
    const trimmed = currentPrefix.replace(/\/$/, '');
    const idx = trimmed.lastIndexOf('/');
    currentPrefix = idx === -1 ? '' : trimmed.slice(0, idx + 1);
    await refreshListing();
  }

  async function refreshListing() {
    if (!selectedBucket) return;
    loading = true;
    listingError = null;
    try {
      const api = await getApi();
      if (!api) return;
      const listing = await api.ListArtifactsFolder(selectedBucket.key, currentPrefix);
      folders = (listing.folders ?? []) as Folder[];
      files = (listing.files ?? []) as File[];
    } catch (err) {
      listingError = err instanceof Error ? err.message : String(err);
      folders = [];
      files = [];
    } finally {
      loading = false;
    }
  }

  async function openFile(file: File) {
    if (!selectedBucket) return;
    try {
      const api = await getApi();
      if (!api) return;
      const { url } = await api.PresignArtifactURL(selectedBucket.key, file.key, 'get', 600);
      // Open in a new window — the presigned URL works directly against
      // MinIO so the browser handles the download natively.
      window.open(url, '_blank');
    } catch (err) {
      notifications.error(`Couldn't open ${file.name}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function deleteFile(file: File) {
    if (!selectedBucket) return;
    if (!window.confirm(`Delete ${file.name}?`)) return;
    try {
      const api = await getApi();
      if (!api) return;
      await api.DeleteArtifactObject(selectedBucket.key, file.key);
      notifications.success(`Deleted ${file.name}`);
      await refreshListing();
    } catch (err) {
      notifications.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // Derived breadcrumb segments. The profile prefix is hidden — operators
  // never need to see "personal/" in front of every path.
  const segments = $derived(
    currentPrefix
      ? currentPrefix.replace(/\/$/, '').split('/').filter(Boolean)
      : []
  );

  function fmtSize(b: number): string {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
    return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function fmtTime(ms: number): string {
    if (!ms) return '';
    return new Date(ms).toLocaleString();
  }

  onMount(() => {
    void bootstrap();
  });
</script>

<div class="panel">
  <header class="panel-header">
    <h2>Files</h2>
    {#if health?.ok}
      <button class="btn-refresh" onclick={refreshListing} title="Refresh">↻</button>
    {/if}
  </header>

  {#if health === null}
    <div class="centered"><Spinner /></div>
  {:else if !health.ok && health.reason === 'disabled'}
    <EmptyState
      title="MinIO is not enabled"
      message="Set CL_MINIO__ENABLED=true alongside the endpoint + per-profile keys in ~/.config/phantom-ink/brainbox/brainbox.env, then restart the daemon. See docker/minio/bootstrap-buckets.sh for the bootstrap script."
    />
  {:else if !health.ok}
    <EmptyState
      title="MinIO is unreachable"
      message="Daemon couldn't reach the configured endpoint: {health.reason ?? 'unknown error'}"
    />
  {:else}
    <div class="browser">
      <aside class="bucket-list">
        <div class="aside-label">Buckets</div>
        {#each buckets as bucket (bucket.key)}
          <button
            class="bucket-item"
            class:active={selectedBucket?.key === bucket.key}
            onclick={() => openBucket(bucket)}
          >
            <span class="bucket-label">{bucket.label}</span>
            <span class="bucket-name">{bucket.name}</span>
          </button>
        {/each}
        <div class="endpoint-info">
          <div class="dim">endpoint</div>
          <div class="mono small">{health.endpoint}</div>
          {#if health.profile_prefix}
            <div class="dim" style="margin-top: 6px;">profile</div>
            <div class="mono small">{health.profile_prefix}</div>
          {/if}
        </div>
      </aside>

      <section class="listing">
        <div class="breadcrumb">
          <button class="crumb" disabled={!currentPrefix} onclick={() => { currentPrefix = ''; void refreshListing(); }}>
            {selectedBucket?.label ?? ''}
          </button>
          {#each segments as seg, i (i + seg)}
            <span class="crumb-sep">/</span>
            <button
              class="crumb"
              onclick={() => {
                currentPrefix = segments.slice(0, i + 1).join('/') + '/';
                void refreshListing();
              }}
            >{seg}</button>
          {/each}
          {#if currentPrefix}
            <button class="btn-up" onclick={goUp} title="Up one level">↑</button>
          {/if}
        </div>

        {#if loading}
          <div class="centered"><Spinner /></div>
        {:else if listingError}
          <div class="error">{listingError}</div>
        {:else if folders.length === 0 && files.length === 0}
          <EmptyState title="Empty" message="No folders or files at this prefix." />
        {:else}
          <div class="grid">
            {#each folders as folder (folder.prefix)}
              <button class="row folder-row" onclick={() => enterFolder(folder)}>
                <span class="row-icon">📁</span>
                <span class="row-name">{folder.name}</span>
                <span class="row-meta dim">folder</span>
              </button>
            {/each}
            {#each files as file (file.key)}
              <div class="row file-row">
                <span class="row-icon">📄</span>
                <button class="row-name link" onclick={() => openFile(file)}>{file.name}</button>
                <span class="row-meta dim">{fmtSize(file.size)} · {fmtTime(file.last_modified_ms)}</span>
                <button class="row-delete" onclick={() => deleteFile(file)} title="Delete">×</button>
              </div>
            {/each}
          </div>
        {/if}
      </section>
    </div>
  {/if}
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }
  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
  }
  .panel-header h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
  }
  .btn-refresh {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    padding: 4px 10px;
    border-radius: var(--r-sm);
    cursor: pointer;
    font-size: 14px;
  }
  .btn-refresh:hover { color: var(--text); }

  .centered { display: grid; place-items: center; padding: 48px 0; }

  .browser {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 16px;
    padding: 16px 24px;
  }
  .bucket-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    overflow: auto;
    padding-right: 8px;
    border-right: 1px solid var(--border);
  }
  .aside-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    padding: 0 8px 8px;
  }
  .bucket-item {
    background: transparent;
    border: 1px solid transparent;
    text-align: left;
    padding: 8px 10px;
    border-radius: var(--r-sm);
    cursor: pointer;
    color: var(--text);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .bucket-item:hover { background: var(--bg-hover); }
  .bucket-item.active {
    background: var(--bg-elev);
    border-color: var(--border);
  }
  .bucket-label { font-weight: 600; font-size: 13px; }
  .bucket-name {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
  }
  .endpoint-info {
    margin-top: auto;
    padding: 12px 8px;
    border-top: 1px solid var(--border);
    font-size: 11px;
  }
  .dim { color: var(--text-muted); }
  .mono.small { font-family: var(--font-mono); font-size: 11px; color: var(--text); word-break: break-all; }

  .listing {
    display: flex;
    flex-direction: column;
    min-height: 0;
    gap: 12px;
  }
  .breadcrumb {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 6px 10px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .crumb {
    background: transparent;
    border: none;
    color: var(--accent);
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: inherit;
    font-size: inherit;
  }
  .crumb:hover:not(:disabled) { background: var(--bg-hover); }
  .crumb:disabled { color: var(--text); cursor: default; }
  .crumb-sep { color: var(--text-faint); }
  .btn-up {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    padding: 2px 8px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 12px;
  }
  .btn-up:hover { color: var(--text); }

  .grid {
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow: auto;
    min-height: 0;
  }
  .row {
    display: grid;
    grid-template-columns: 24px 1fr auto auto;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--r-sm);
    color: var(--text);
    font-family: inherit;
    font-size: 13px;
    text-align: left;
  }
  .row:hover {
    background: var(--bg-hover);
    border-color: var(--border);
  }
  .folder-row { cursor: pointer; }
  .row-icon { font-size: 14px; }
  .row-name {
    background: transparent;
    border: none;
    color: inherit;
    padding: 0;
    cursor: pointer;
    text-align: left;
    font-family: inherit;
    font-size: inherit;
  }
  .row-name.link { color: var(--accent); }
  .row-name.link:hover { text-decoration: underline; }
  .row-meta { font-size: 11px; white-space: nowrap; }
  .row-delete {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 2px 8px;
    font-size: 14px;
    border-radius: 3px;
  }
  .row-delete:hover { color: var(--fail); background: var(--fail-soft); }

  .error {
    color: var(--fail);
    padding: 12px 16px;
    background: var(--fail-soft);
    border-left: 2px solid var(--fail);
    border-radius: var(--r-sm);
    font-family: var(--font-mono);
    font-size: 12px;
  }
</style>
