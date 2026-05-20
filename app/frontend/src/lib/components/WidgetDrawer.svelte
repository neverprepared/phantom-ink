<script lang="ts">
  import type { WidgetInstance, WidgetKind, StatCounterConfig, CustomCounterConfig, ScriptMetricConfig, HttpMetricConfig } from '../widgets/types';

  let {
    open,
    onClose,
    onAdd,
    onRemove,
    onReset,
    widgets,
  }: {
    open: boolean;
    onClose: () => void;
    onAdd: (w: WidgetInstance) => void;
    onRemove: (id: string) => void;
    onReset: () => void;
    widgets: WidgetInstance[];
  } = $props();

  let tab = $state<'add' | 'manage'>('add');

  // --- add form state ---
  let addKind = $state<WidgetKind>('stat-counter');

  // stat-counter fields
  let scLabel     = $state('');
  let scColor     = $state<StatCounterConfig['color']>('default');
  let scNavTarget = $state('');
  let scDataKey   = $state<StatCounterConfig['dataKey']>('activeSessions');

  // custom-counter fields
  let ccLabel    = $state('');
  let ccApi      = $state<CustomCounterConfig['api']>('hub_tasks');
  let ccStatus   = $state('');
  let ccColor    = $state('');

  // script-metric fields
  let smLabel    = $state('');
  let smCommand  = $state('');
  let smColor    = $state('');
  let smInterval = $state('60');

  // http-metric fields
  let hmLabel    = $state('');
  let hmUrl      = $state('');
  let hmPath     = $state('');
  let hmHeader   = $state('');
  let hmColor    = $state('');
  let hmInterval = $state('60');

  const WIDGET_KINDS: { kind: WidgetKind; label: string; desc: string }[] = [
    { kind: 'stat-counter',     label: 'Stat Counter',       desc: 'Shows a live count from shared dashboard data.' },
    { kind: 'custom-counter',   label: 'Custom Counter',     desc: 'Fetches a count from any API endpoint.' },
    { kind: 'dispatch-form',    label: 'Dispatch Form',      desc: 'Submit a task to a hub agent.' },
    { kind: 'chains-list',      label: 'Scheduled Chains',   desc: 'Upcoming scheduled chain fires.' },
    { kind: 'action-items',     label: 'Action Items',       desc: 'System alerts and action items.' },
    { kind: 'resource-monitor', label: 'Resource Monitor',   desc: 'CPU, memory, and container stats.' },
    { kind: 'script-metric',    label: 'Script Metric',      desc: 'Run a shell command, display its integer output.' },
    { kind: 'http-metric',      label: 'HTTP Metric',        desc: 'Poll a JSON endpoint, extract a number.' },
  ];

  const DATA_KEYS: { key: StatCounterConfig['dataKey']; label: string }[] = [
    { key: 'activeSessions', label: 'Active Sessions' },
    { key: 'runningTasks',   label: 'Running Tasks' },
    { key: 'failedTasks',    label: 'Failed Tasks (24h)' },
    { key: 'scheduledFires', label: 'Scheduled Fires' },
    { key: 'actionItems',    label: 'Action Items' },
  ];

  const COLORS: { val: StatCounterConfig['color']; label: string }[] = [
    { val: 'default', label: 'Default' },
    { val: 'green',   label: 'Green' },
    { val: 'blue',    label: 'Blue' },
    { val: 'red',     label: 'Red' },
    { val: 'orange',  label: 'Orange' },
    { val: 'muted',   label: 'Muted' },
  ];

  const NAV_TARGETS = ['', 'sessions', 'timeline', 'agents', 'repos', 'chains', 'conversations', 'playbooks'];

  function handleAdd() {
    const id = crypto.randomUUID();
    let widget: WidgetInstance;

    if (addKind === 'stat-counter') {
      widget = {
        id, kind: 'stat-counter',
        config: { label: scLabel || 'Counter', color: scColor, navTarget: scNavTarget || undefined, dataKey: scDataKey },
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'custom-counter') {
      widget = {
        id, kind: 'custom-counter',
        config: { label: ccLabel || 'Count', api: ccApi, filter: ccStatus ? { status: ccStatus } : {}, color: ccColor || undefined },
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'script-metric') {
      widget = {
        id, kind: 'script-metric',
        config: { label: smLabel || 'METRIC', command: smCommand, color: smColor || undefined, interval: parseInt(smInterval) || 60 } satisfies ScriptMetricConfig,
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'http-metric') {
      widget = {
        id, kind: 'http-metric',
        config: { label: hmLabel || 'METRIC', url: hmUrl, path: hmPath || undefined, header: hmHeader || undefined, color: hmColor || undefined, interval: parseInt(hmInterval) || 60 } satisfies HttpMetricConfig,
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else {
      const SIZE: Record<WidgetKind, { w: number; h: number; minW: number; minH: number }> = {
        'stat-counter':     { w: 2, h: 2, minW: 2, minH: 2 },
        'custom-counter':   { w: 2, h: 2, minW: 2, minH: 2 },
        'script-metric':    { w: 2, h: 2, minW: 2, minH: 2 },
        'http-metric':      { w: 2, h: 2, minW: 2, minH: 2 },
        'dispatch-form':    { w: 6, h: 4, minW: 4, minH: 3 },
        'chains-list':      { w: 3, h: 4, minW: 2, minH: 2 },
        'action-items':     { w: 3, h: 4, minW: 2, minH: 2 },
        'resource-monitor': { w: 12, h: 4, minW: 4, minH: 3 },
      };
      widget = { id, kind: addKind, config: {}, x: 0, y: 999, ...SIZE[addKind] };
    }
    onAdd(widget);
  }

  const KIND_LABELS: Record<WidgetKind, string> = {
    'stat-counter':     'Stat Counter',
    'custom-counter':   'Custom Counter',
    'script-metric':    'Script Metric',
    'http-metric':      'HTTP Metric',
    'dispatch-form':    'Dispatch Form',
    'chains-list':      'Scheduled Chains',
    'action-items':     'Action Items',
    'resource-monitor': 'Resource Monitor',
  };
</script>

{#if open}
  <!-- backdrop -->
  <div class="backdrop" onclick={onClose} aria-hidden="true"></div>

  <div class="drawer" role="dialog" aria-label="Widget settings">
    <div class="drawer-header">
      <span class="drawer-title">Widgets</span>
      <button class="close-btn" onclick={onClose} aria-label="Close">✕</button>
    </div>

    <div class="tabs">
      <button class="tab" class:active={tab === 'add'} onclick={() => tab = 'add'}>Add</button>
      <button class="tab" class:active={tab === 'manage'} onclick={() => tab = 'manage'}>Manage</button>
    </div>

    <div class="drawer-body">
      {#if tab === 'add'}
        <!-- Kind picker -->
        <div class="kind-list">
          {#each WIDGET_KINDS as k (k.kind)}
            <button
              class="kind-card"
              class:selected={addKind === k.kind}
              onclick={() => addKind = k.kind}
            >
              <span class="kind-label">{k.label}</span>
              <span class="kind-desc">{k.desc}</span>
            </button>
          {/each}
        </div>

        <!-- Config form -->
        <div class="config-form">
          {#if addKind === 'stat-counter'}
            <div class="field">
              <label>Label</label>
              <input type="text" bind:value={scLabel} placeholder="MY COUNTER" />
            </div>
            <div class="field">
              <label>Data</label>
              <select bind:value={scDataKey}>
                {#each DATA_KEYS as d (d.key)}
                  <option value={d.key}>{d.label}</option>
                {/each}
              </select>
            </div>
            <div class="field">
              <label>Color</label>
              <select bind:value={scColor}>
                {#each COLORS as c (c.val)}
                  <option value={c.val}>{c.label}</option>
                {/each}
              </select>
            </div>
            <div class="field">
              <label>Navigate to</label>
              <select bind:value={scNavTarget}>
                {#each NAV_TARGETS as t (t)}
                  <option value={t}>{t || '(none)'}</option>
                {/each}
              </select>
            </div>
          {:else if addKind === 'custom-counter'}
            <div class="field">
              <label>Label</label>
              <input type="text" bind:value={ccLabel} placeholder="MY METRIC" />
            </div>
            <div class="field">
              <label>API</label>
              <select bind:value={ccApi}>
                <option value="hub_tasks">Hub Tasks</option>
                <option value="sessions">Sessions</option>
                <option value="repos">Repos</option>
              </select>
            </div>
            <div class="field">
              <label>Status filter</label>
              <input type="text" bind:value={ccStatus} placeholder="running, failed, … (optional)" />
            </div>
            <div class="field">
              <label>Color (CSS)</label>
              <input type="text" bind:value={ccColor} placeholder="#22c55e or var(--color-success)" />
            </div>
          {:else if addKind === 'script-metric'}
            <div class="field">
              <label>Label</label>
              <input type="text" bind:value={smLabel} placeholder="UNREAD EMAILS" />
            </div>
            <div class="field">
              <label>Command</label>
              <textarea bind:value={smCommand} rows="3" placeholder="echo 42&#10;# stdout must be a single integer"></textarea>
            </div>
            <div class="field">
              <label>Color (CSS)</label>
              <input type="text" bind:value={smColor} placeholder="#22c55e or var(--color-success)" />
            </div>
            <div class="field">
              <label>Interval (seconds)</label>
              <input type="number" bind:value={smInterval} min="10" max="3600" />
            </div>
          {:else if addKind === 'http-metric'}
            <div class="field">
              <label>Label</label>
              <input type="text" bind:value={hmLabel} placeholder="OPEN PRS" />
            </div>
            <div class="field">
              <label>URL</label>
              <input type="text" bind:value={hmUrl} placeholder="https://api.example.com/count" />
            </div>
            <div class="field">
              <label>JSON path</label>
              <input type="text" bind:value={hmPath} placeholder="data.total (leave blank for root)" />
            </div>
            <div class="field">
              <label>Header (optional)</label>
              <input type="text" bind:value={hmHeader} placeholder="Authorization: Bearer TOKEN" />
            </div>
            <div class="field">
              <label>Color (CSS)</label>
              <input type="text" bind:value={hmColor} placeholder="#22c55e or var(--color-info)" />
            </div>
            <div class="field">
              <label>Interval (seconds)</label>
              <input type="number" bind:value={hmInterval} min="10" max="3600" />
            </div>
          {:else}
            <p class="no-config">No configuration needed.</p>
          {/if}

          <button class="btn-add" onclick={handleAdd}>Add widget</button>
        </div>

      {:else}
        <!-- Manage tab -->
        <div class="widget-list">
          {#each widgets as w (w.id)}
            <div class="widget-row">
              <span class="widget-kind">{KIND_LABELS[w.kind]}</span>
              {#if 'label' in w.config}
                <span class="widget-label">{(w.config as any).label}</span>
              {/if}
              <button class="btn-remove" onclick={() => onRemove(w.id)} aria-label="Remove widget">✕</button>
            </div>
          {/each}
          {#if widgets.length === 0}
            <p class="empty">No widgets.</p>
          {/if}
        </div>

        <button class="btn-reset" onclick={onReset}>Reset to default layout</button>
      {/if}
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 199;
  }

  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: 320px;
    background: var(--color-bg-secondary);
    border-left: 1px solid var(--color-border-primary);
    z-index: 200;
    display: flex;
    flex-direction: column;
    box-shadow: -4px 0 24px rgba(0,0,0,0.15);
  }

  .drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .drawer-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    cursor: pointer;
    font-size: 14px;
    padding: 4px;
  }
  .close-btn:hover { color: var(--color-text-primary); }

  .tabs {
    display: flex;
    border-bottom: 1px solid var(--color-border-primary);
    flex-shrink: 0;
  }

  .tab {
    flex: 1;
    padding: 10px;
    font-size: 12px;
    font-weight: 500;
    background: none;
    border: none;
    cursor: pointer;
    color: var(--color-text-tertiary);
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
    font-family: inherit;
  }
  .tab:hover { color: var(--color-text-secondary); }
  .tab.active {
    color: var(--color-accent);
    border-bottom-color: var(--color-accent);
  }

  .drawer-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  /* Kind picker */
  .kind-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .kind-card {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 10px 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    cursor: pointer;
    text-align: left;
    font-family: inherit;
    transition: all 0.15s;
  }
  .kind-card:hover { border-color: var(--color-border-secondary); }
  .kind-card.selected {
    border-color: var(--color-accent);
    background: rgba(234, 179, 8, 0.05);
  }

  .kind-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text-primary);
  }

  .kind-desc {
    font-size: 11px;
    color: var(--color-text-tertiary);
  }

  /* Config form */
  .config-form {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .field label {
    font-size: 11px;
    font-weight: 500;
    color: var(--color-text-secondary);
  }

  .field input,
  .field select,
  .field textarea {
    padding: 6px 8px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-sm);
    color: var(--color-text-primary);
    font-size: 12px;
    font-family: inherit;
    outline: none;
  }
  .field input:focus,
  .field select:focus,
  .field textarea:focus { border-color: var(--color-accent); }
  .field textarea { resize: vertical; font-family: var(--font-mono); }

  .no-config {
    font-size: 12px;
    color: var(--color-text-tertiary);
    margin: 0;
  }

  .btn-add {
    padding: 8px 16px;
    background: var(--color-accent);
    color: white;
    border: none;
    border-radius: var(--radius-md);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    font-family: inherit;
    margin-top: 4px;
  }
  .btn-add:hover { opacity: 0.85; }

  /* Manage tab */
  .widget-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .widget-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
  }

  .widget-kind {
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-primary);
    flex: 1;
    min-width: 0;
  }

  .widget-label {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    max-width: 80px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .btn-remove {
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    cursor: pointer;
    font-size: 12px;
    padding: 2px 4px;
    flex-shrink: 0;
  }
  .btn-remove:hover { color: var(--color-error); }

  .empty {
    font-size: 12px;
    color: var(--color-text-tertiary);
    margin: 0;
  }

  .btn-reset {
    padding: 8px 16px;
    background: none;
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
    font-size: 12px;
    color: var(--color-text-secondary);
    cursor: pointer;
    font-family: inherit;
    margin-top: auto;
  }
  .btn-reset:hover { background: var(--color-surface-hover); }
</style>
