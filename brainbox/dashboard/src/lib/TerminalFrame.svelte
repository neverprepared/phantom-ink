<script>
  import { stopSession } from './api.js';
  import { notifications } from './notifications.svelte.js';

  let { session, onUpdate } = $props();

  let confirmStop = $state(false);
  let confirmTimeout = null;

  function resetConfirm() {
    if (confirmTimeout) clearTimeout(confirmTimeout);
    confirmStop = false;
  }

  async function handleStop(e) {
    e.preventDefault();
    if (confirmStop) {
      resetConfirm();
      try {
        await stopSession(session.name);
        onUpdate();
      } catch (err) {
        notifications.error(`Failed to stop session: ${err.message}`);
      }
      return;
    }
    confirmStop = true;
    confirmTimeout = setTimeout(resetConfirm, 3000);
  }

  let iframeSrc = $derived(session.url);
  let refreshKey = $state(0);

  function refreshFrame(e) {
    e.preventDefault();
    refreshKey++;
  }

  let displayName = $derived(session.session_name || session.name);
</script>

<div class="frame">
  <div class="frame-bar">
    <span>{displayName}</span>
    <div class="frame-actions">
      <button
        class="frame-stop frame-action-btn"
        onclick={handleStop}
        aria-label={confirmStop ? `Confirm stop ${displayName}` : `Stop ${displayName}`}
      >{confirmStop ? 'stop?' : 'stop'}</button>
      <button
        class="frame-action-btn"
        onclick={refreshFrame}
        aria-label={`Refresh ${displayName}`}
      >refresh</button>
      <a
        href={session.url}
        target="_blank"
        aria-label={`Open ${displayName} in new tab`}
      >open</a>
    </div>
  </div>
  {#key refreshKey}
    <iframe src={iframeSrc} title={displayName}></iframe>
  {/key}
</div>

<style>
  .frame {
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 8px;
    overflow: hidden;
  }
  .frame-bar {
    padding: 10px 16px;
    border-bottom: 1px solid #1e293b;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    color: #94a3b8;
  }
  .frame-actions {
    display: flex;
    gap: 12px;
  }
  .frame-bar a, .frame-action-btn {
    color: #f59e0b;
    text-decoration: none;
    font-size: 12px;
  }
  .frame-action-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-family: inherit;
    padding: 0;
  }
  .frame-bar a:hover, .frame-action-btn:hover { text-decoration: underline; }
  iframe {
    width: 100%;
    height: 450px;
    max-height: 450px;
    border: none;
    background: #000;
  }
</style>
