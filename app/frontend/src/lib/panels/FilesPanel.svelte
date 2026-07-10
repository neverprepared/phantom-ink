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
  import { formatBytes } from '../utils/format';
  import { getApi } from '../utils/api';
  import { notifications } from '../notifications.svelte';
  import { profileState } from '../stores.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Spinner from '../components/Spinner.svelte';

  interface Bucket {
    key: string;
    name: string;
    label: string;
    scope_prefix: string;
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

  let health = $state<{ ok: boolean; reason?: string; endpoint?: string; public_endpoint?: string; profile_prefix?: string } | null>(null);
  // The MinIO address this app fetches presigned URLs from: the app's
  // MinIO integration entry (local/remote per its toggle), falling back
  // to the daemon's public endpoint.
  let browserAddress = $state('');
  let buckets = $state<Bucket[]>([]);
  let selectedBucket = $state<Bucket | null>(null);
  let currentPrefix = $state<string>('');
  let folders = $state<Folder[]>([]);
  let files = $state<File[]>([]);
  let loading = $state(false);
  let listingError = $state<string | null>(null);

  // --- Search (whole-bucket, profile-scoped server-side) ---
  let query = $state('');
  let searching = $state(false);
  let searchResults = $state<File[]>([]);
  let searchTruncated = $state(false);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  // --- Preview drawer ---
  type Preview = {
    kind: 'text' | 'image' | 'unsupported';
    content_type: string;
    size: number;
    text?: string;
    data_uri?: string;
    truncated?: boolean;
    reason?: string;
  };
  let selected = $state<File | null>(null);
  let preview = $state<Preview | null>(null);
  let previewLoading = $state(false);
  let downloading = $state(false);

  // The Files view follows the app's active profile: null ("all") shows
  // every bucket unscoped; a specific profile shows only its buckets/
  // slices, rooted at each bucket's scope_prefix. profileState.active is
  // a Profile object — the API wants the NAME string.
  let activeProfile = $derived(profileState.active?.name ?? '');

  async function bootstrap() {
    const api = await getApi();
    if (!api) return;
    try {
      health = await api.GetArtifactsHealth();
      browserAddress = (await api.GetMinioBrowserAddress()) || health?.public_endpoint || health?.endpoint || '';
      if (health?.ok) {
        buckets = (await api.ListArtifactsBuckets(activeProfile)) ?? [];
        closePreview();
        clearSearch();
        if (buckets.length > 0) {
          await openBucket(buckets[0]);
        } else {
          selectedBucket = null;
          folders = [];
          files = [];
        }
      }
    } catch (err) {
      health = { ok: false, reason: err instanceof Error ? err.message : String(err) };
    }
  }

  // Re-scope when the operator switches profile tabs.
  let lastProfile = $state<string | null>(null);
  $effect(() => {
    const p = activeProfile;
    if (lastProfile !== null && p !== lastProfile) {
      void bootstrap();
    }
    lastProfile = p;
  });

  async function openBucket(bucket: Bucket) {
    selectedBucket = bucket;
    currentPrefix = bucket.scope_prefix ?? '';
    clearSearch();
    closePreview();
    await refreshListing();
  }

  // Root of the current view — the bucket's scope_prefix for the active
  // profile. Navigation never goes above it; displays are relative to it.
  const scopeRoot = $derived(selectedBucket?.scope_prefix ?? '');

  function relPath(key: string): string {
    return scopeRoot && key.startsWith(scopeRoot) ? key.slice(scopeRoot.length) : key;
  }

  function clearSearch() {
    query = '';
    searchResults = [];
    searchTruncated = false;
    if (searchTimer) clearTimeout(searchTimer);
  }

  function onQueryInput() {
    if (searchTimer) clearTimeout(searchTimer);
    const q = query.trim();
    if (!q) {
      searchResults = [];
      searchTruncated = false;
      return;
    }
    searchTimer = setTimeout(() => void runSearch(q), 300);
  }

  async function runSearch(q: string) {
    if (!selectedBucket) return;
    searching = true;
    try {
      const api = await getApi();
      if (!api) return;
      const res = await api.SearchArtifactsFiles(selectedBucket.key, q, scopeRoot);
      // Ignore stale responses — only apply if the box still holds this query.
      if (query.trim() === q) {
        searchResults = (res?.files ?? []) as File[];
        searchTruncated = !!res?.truncated;
      }
    } catch (err) {
      notifications.error(`Search failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      searching = false;
    }
  }

  function closePreview() {
    selected = null;
    preview = null;
  }

  async function selectFile(file: File) {
    selected = file;
    preview = null;
    previewLoading = true;
    try {
      const api = await getApi();
      if (!api) return;
      if (selectedBucket) {
        preview = (await api.GetArtifactPreview(selectedBucket.key, file.key)) as Preview;
      }
    } catch (err) {
      preview = {
        kind: 'unsupported', content_type: '', size: file.size,
        reason: err instanceof Error ? err.message : String(err),
      };
    } finally {
      previewLoading = false;
    }
  }

  async function downloadFile(file: File) {
    if (!selectedBucket) return;
    downloading = true;
    try {
      const api = await getApi();
      if (!api) return;
      const path = await api.DownloadArtifactObject(selectedBucket.key, file.key, file.name);
      if (path) notifications.success(`Saved to ${path}`);
    } catch (err) {
      notifications.error(`Download failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      downloading = false;
    }
  }

  async function enterFolder(folder: Folder) {
    clearSearch();
    // Prefixes are raw bucket keys end-to-end since the browser refactor.
    currentPrefix = folder.prefix;
    await refreshListing();
  }

  async function goUp() {
    if (!currentPrefix || currentPrefix === scopeRoot) return;
    const trimmed = currentPrefix.replace(/\/$/, '');
    const idx = trimmed.lastIndexOf('/');
    const up = idx === -1 ? '' : trimmed.slice(0, idx + 1);
    currentPrefix = up.length < scopeRoot.length ? scopeRoot : up;
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
    // Deletes are soft — the daemon moves the object to the bucket's
    // .trash/ namespace. Inside .trash/ a delete is permanent.
    const inTrash = file.key.startsWith('.trash/');
    const prompt = inTrash
      ? `Permanently delete ${file.name}? This cannot be undone.`
      : `Move ${file.name} to trash?`;
    if (!window.confirm(prompt)) return;
    try {
      const api = await getApi();
      if (!api) return;
      await api.DeleteArtifactObject(selectedBucket.key, file.key);
      notifications.success(inTrash ? `Permanently deleted ${file.name}` : `Moved ${file.name} to trash`);
      await refreshListing();
    } catch (err) {
      notifications.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // Derived breadcrumb segments, relative to the scope root — the active
  // profile's prefix is the floor, not a visible path component.
  const segments = $derived(
    relPath(currentPrefix)
      ? relPath(currentPrefix).replace(/\/$/, '').split('/').filter(Boolean)
      : []
  );

  function fmtSize(b: number): string {
    return formatBytes(b);
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
      <div class="header-actions">
        <input
          class="search-input"
          type="search"
          placeholder="search {selectedBucket?.label ?? 'bucket'}…"
          bind:value={query}
          oninput={onQueryInput}
          onkeydown={(e) => { if (e.key === 'Escape') clearSearch(); }}
        />
        <button class="btn-refresh" onclick={refreshListing} title="Refresh">↻</button>
      </div>
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
    <div class="browser" class:with-detail={!!selected}>
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
          <div class="mono small">{browserAddress}</div>
          <div class="dim" style="margin-top: 6px;">profile</div>
          <div class="mono small">{activeProfile || 'all'}</div>
        </div>
      </aside>

      <section class="listing">
        {#if query.trim()}
          <div class="search-status">
            {#if searching}
              <Spinner />
            {:else}
              <span class="dim">{searchResults.length} result{searchResults.length === 1 ? '' : 's'} for “{query.trim()}”{searchTruncated ? ` — showing first ${searchResults.length}` : ''}</span>
            {/if}
            <button class="crumb" onclick={clearSearch}>clear</button>
          </div>
          <div class="grid">
            {#each searchResults as file (file.key)}
              <div class="row file-row" class:selected={selected?.key === file.key}>
                <span class="row-icon">📄</span>
                <button class="row-name link" onclick={() => selectFile(file)}>{relPath(file.key)}</button>
                <span class="row-meta dim">{fmtSize(file.size)} · {fmtTime(file.last_modified_ms)}</span>
              </div>
            {:else}
              {#if !searching}
                <EmptyState title="No matches" message="No object keys contain “{query.trim()}”." />
              {/if}
            {/each}
          </div>
        {:else}
        <div class="breadcrumb">
          <button class="crumb" disabled={currentPrefix === scopeRoot} onclick={() => { currentPrefix = scopeRoot; void refreshListing(); }}>
            {selectedBucket?.label ?? ''}
          </button>
          {#each segments as seg, i (i + seg)}
            <span class="crumb-sep">/</span>
            <button
              class="crumb"
              onclick={() => {
                currentPrefix = scopeRoot + segments.slice(0, i + 1).join('/') + '/';
                void refreshListing();
              }}
            >{seg}</button>
          {/each}
          {#if currentPrefix !== scopeRoot}
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
              <div class="row file-row" class:selected={selected?.key === file.key}>
                <span class="row-icon">📄</span>
                <button class="row-name link" onclick={() => selectFile(file)}>{file.name}</button>
                <span class="row-meta dim">{fmtSize(file.size)} · {fmtTime(file.last_modified_ms)}</span>
                <button class="row-delete" onclick={() => deleteFile(file)} title="Delete">×</button>
              </div>
            {/each}
          </div>
        {/if}
        {/if}
      </section>

      {#if selected}
        <aside class="detail">
          <div class="detail-head">
            <span class="detail-name" title={selected.key}>{selected.name}</span>
            <button class="detail-close" onclick={closePreview} title="Close">×</button>
          </div>
          <div class="detail-meta">
            <span class="dim">{fmtSize(selected.size)} · {fmtTime(selected.last_modified_ms)}</span>
            {#if preview?.content_type}<span class="mono small">{preview.content_type}</span>{/if}
          </div>
          <div class="detail-actions">
            <button class="btn-action" onclick={() => downloadFile(selected!)} disabled={downloading}>
              {downloading ? 'saving…' : 'download'}
            </button>
            <button class="btn-action ghost" onclick={() => openFile(selected!)}>open</button>
            <button class="btn-action ghost danger" onclick={() => { const f = selected!; closePreview(); void deleteFile(f); }}>delete</button>
          </div>
          <div class="detail-preview">
            {#if previewLoading}
              <div class="centered"><Spinner /></div>
            {:else if preview?.kind === 'text'}
              <pre class="preview-text">{preview.text}</pre>
              {#if preview.truncated}<div class="dim preview-note">preview truncated</div>{/if}
            {:else if preview?.kind === 'image'}
              <img class="preview-image" src={preview.data_uri} alt={selected.name} />
            {:else}
              <EmptyState title="No preview" message={preview?.reason ?? 'This file type has no inline preview.'} />
            {/if}
          </div>
        </aside>
      {/if}
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
  .browser.with-detail {
    grid-template-columns: 220px 1fr minmax(320px, 420px);
  }

  .header-actions { display: flex; align-items: center; gap: 8px; }
  .search-input {
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: var(--r-sm);
    padding: 5px 10px;
    font-size: 12px;
    width: 240px;
  }
  .search-input:focus { outline: none; border-color: var(--accent); }
  .search-status {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    font-size: 12px;
  }

  .file-row.selected {
    background: var(--accent-soft);
    border-color: var(--accent);
  }

  .detail {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 0;
    border-left: 1px solid var(--border);
    padding-left: 16px;
  }
  .detail-head { display: flex; align-items: center; gap: 8px; }
  .detail-name {
    font-weight: 600;
    font-size: 13px;
    word-break: break-all;
  }
  .detail-close {
    margin-left: auto;
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 16px;
    padding: 0 4px;
  }
  .detail-close:hover { color: var(--text); }
  .detail-meta { display: flex; flex-direction: column; gap: 2px; font-size: 11px; }
  .detail-actions { display: flex; gap: 6px; }
  .btn-action {
    background: var(--accent-soft);
    border: 1px solid var(--accent);
    color: var(--accent);
    font-size: 11px;
    padding: 3px 12px;
    border-radius: var(--r-sm);
    cursor: pointer;
  }
  .btn-action:disabled { opacity: 0.5; cursor: default; }
  .btn-action.ghost { background: transparent; border-color: var(--border); color: var(--text-muted); }
  .btn-action.ghost:hover { color: var(--text); }
  .btn-action.ghost.danger:hover { color: var(--fail); border-color: var(--fail); }
  .detail-preview {
    flex: 1;
    min-height: 0;
    overflow: auto;
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    background: var(--bg-sunken);
  }
  .preview-text {
    margin: 0;
    padding: 10px 12px;
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .preview-image { max-width: 100%; display: block; padding: 8px; }
  .preview-note { padding: 4px 12px 8px; font-size: 10px; }
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
