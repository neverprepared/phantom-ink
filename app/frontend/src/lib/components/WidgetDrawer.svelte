<script lang="ts">
  import type { WidgetInstance, WidgetKind, StatCounterConfig, CustomCounterConfig, ScriptMetricConfig, HttpMetricConfig, StreamWidgetConfig, OpenSearchMetricConfig, OpenSearchMetric } from '../widgets/types';
  import { featureFlags } from '../stores.svelte';

  let {
    open,
    onClose,
    onAdd,
    onUpdate,
    onRemove,
    onReset,
    widgets,
    editTarget,
    onEditConsumed,
  }: {
    open: boolean;
    onClose: () => void;
    onAdd: (w: WidgetInstance) => void;
    onUpdate: (w: WidgetInstance) => void;
    onRemove: (id: string) => void;
    onReset: () => void;
    widgets: WidgetInstance[];
    editTarget?: WidgetInstance | null;
    onEditConsumed?: () => void;
  } = $props();

  let tab = $state<'add' | 'manage'>('add');

  // When parent passes a widget to edit, jump straight into the edit form.
  $effect(() => {
    if (open && editTarget) {
      tab = 'manage';
      startEdit(editTarget);
      onEditConsumed?.();
    }
  });

  // Disable browser autocorrect / spellcheck / autocomplete on all
  // freeform inputs inside the drawer. Easier than touching every
  // <input> markup site. Runs whenever the drawer opens or the tab
  // changes.
  let drawerEl: HTMLDivElement | undefined = $state();
  $effect(() => {
    if (!open || !drawerEl) return;
    // void-track tab and editingWidget so this re-runs after re-renders.
    void tab; void editingWidget;
    queueMicrotask(() => {
      if (!drawerEl) return;
      const fields = drawerEl.querySelectorAll('input[type="text"], textarea');
      fields.forEach((el) => {
        const e = el as HTMLInputElement | HTMLTextAreaElement;
        e.setAttribute('spellcheck', 'false');
        e.setAttribute('autocomplete', 'off');
        if (!e.hasAttribute('autocorrect')) e.setAttribute('autocorrect', 'off');
        // Don't override per-input autocapitalize (metric labels set "characters").
        if (!e.hasAttribute('autocapitalize')) e.setAttribute('autocapitalize', 'off');
      });
    });
  });

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
  let smLabel     = $state('');
  let smCommand   = $state('');
  let smValueType = $state<'number' | 'string'>('number');
  let smColor     = $state('');
  let smInterval  = $state('60');

  // http-metric fields
  let hmLabel     = $state('');
  let hmUrl       = $state('');
  let hmPath      = $state('');
  let hmHeader    = $state('');
  let hmValueType = $state<'number' | 'string'>('number');
  let hmColor     = $state('');
  let hmInterval  = $state('60');

  // stream fields
  let stLabel   = $state('stream');
  let stProfile = $state('');
  let stTag     = $state('');
  let stLimit   = $state('20');
  let stSources = $state<('task' | 'event')[]>(['task', 'event']);

  // opensearch-metric fields
  let osMetric = $state<OpenSearchMetric>('cost-today');
  let osLabel  = $state('');
  let osColor  = $state<OpenSearchMetricConfig['color']>('default');

  // Metric labels are stored and displayed uppercase for visual consistency
  // with the rest of the dashboard header style.
  const metricLabel = (raw: string, fallback: string): string =>
    (raw?.trim() || fallback).toUpperCase();

  const OS_METRICS: { val: OpenSearchMetric; label: string }[] = [
    { val: 'cost-today',      label: 'Cost Today (USD)' },
    { val: 'tokens-today',    label: 'Tokens Today' },
    { val: 'api-requests-1h', label: 'API Requests (1h)' },
    { val: 'avg-latency-1h',  label: 'Avg API Latency (1h)' },
  ];

  const ALL_WIDGET_KINDS: { kind: WidgetKind; label: string; desc: string; requires?: string }[] = [
    { kind: 'stat-counter',     label: 'Stat Counter',       desc: 'Shows a live count from shared dashboard data.' },
    { kind: 'custom-counter',   label: 'Custom Counter',     desc: 'Fetches a count from any API endpoint.' },
    { kind: 'calendar',         label: 'Calendar',           desc: 'Today / This Week / Overdue task counts with drilldown.' },
    { kind: 'tasks',            label: 'Tasks',              desc: 'Scrollable task list with open/done/all filter.' },
    { kind: 'sessions-mini',    label: 'Live Sessions',      desc: 'Mini list of active and stopped sessions.' },
    { kind: 'notes',            label: 'Scratchpad',         desc: 'Persistent freeform text notes.' },
    { kind: 'dispatch-form',    label: 'Dispatch Form',      desc: 'Submit a task to a hub agent.' },
    { kind: 'action-items',     label: 'Action Items',       desc: 'System alerts and action items.' },
    { kind: 'resource-monitor', label: 'Resource Monitor',   desc: 'CPU, memory, and container stats.' },
    { kind: 'script-metric',    label: 'Script Metric',      desc: 'Run a shell command, display its output as a number or string.' },
    { kind: 'http-metric',      label: 'HTTP Metric',        desc: 'Poll a JSON endpoint, extract a number or string.' },
    { kind: 'stream',           label: 'Stream',             desc: 'Chronological feed of tasks and collected events.' },
    { kind: 'opensearch-metric', label: 'OpenSearch Metric', desc: 'Cost, tokens, requests, or latency from Claude Code telemetry.', requires: 'opensearch' },
  ];

  // Hide widgets whose required integration is not enabled.
  let WIDGET_KINDS = $derived(
    ALL_WIDGET_KINDS.filter(w => !w.requires || featureFlags.isEnabled(w.requires))
  );

  // If the currently selected addKind gets filtered out (flag went off), fall back.
  $effect(() => {
    if (!WIDGET_KINDS.some(w => w.kind === addKind)) addKind = 'stat-counter';
  });

  const DATA_KEYS: { key: StatCounterConfig['dataKey']; label: string }[] = [
    { key: 'activeSessions', label: 'Active Sessions' },
    { key: 'runningTasks',   label: 'Running Tasks' },
    { key: 'failedTasks',    label: 'Failed Tasks (24h)' },
    { key: 'scheduledFires', label: 'Scheduled Fires' },
    { key: 'actionItems',    label: 'Action Items' },
    { key: 'attentionItems', label: 'Needs Attention' },
    { key: 'offlineRunners', label: 'Offline Runners' },
    { key: 'peakQueue1h',    label: 'Peak Queue (1h)' },
  ];

  const COLORS: { val: StatCounterConfig['color']; label: string }[] = [
    { val: 'default', label: 'Default' },
    { val: 'green',   label: 'Green' },
    { val: 'blue',    label: 'Blue' },
    { val: 'red',     label: 'Red' },
    { val: 'orange',  label: 'Orange' },
    { val: 'muted',   label: 'Muted' },
  ];

  const NAV_TARGETS = ['', 'sessions', 'stream', 'agents', 'conversations', 'playbooks'];

  function handleAdd() {
    const id = crypto.randomUUID();
    let widget: WidgetInstance;

    if (addKind === 'stat-counter') {
      widget = {
        id, kind: 'stat-counter',
        config: { label: metricLabel(scLabel, 'COUNTER'), color: scColor, navTarget: scNavTarget || undefined, dataKey: scDataKey },
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'custom-counter') {
      widget = {
        id, kind: 'custom-counter',
        config: { label: metricLabel(ccLabel, 'COUNT'), api: ccApi, filter: ccStatus ? { status: ccStatus } : {}, color: ccColor || undefined },
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'script-metric') {
      widget = {
        id, kind: 'script-metric',
        config: { label: metricLabel(smLabel, 'METRIC'), command: smCommand, valueType: smValueType, color: smColor || undefined, interval: parseInt(smInterval) || 60 } satisfies ScriptMetricConfig,
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'http-metric') {
      widget = {
        id, kind: 'http-metric',
        config: { label: metricLabel(hmLabel, 'METRIC'), url: hmUrl, path: hmPath || undefined, header: hmHeader || undefined, valueType: hmValueType, color: hmColor || undefined, interval: parseInt(hmInterval) || 60 } satisfies HttpMetricConfig,
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else if (addKind === 'stream') {
      widget = {
        id, kind: 'stream',
        config: { label: stLabel || 'stream', profile: stProfile || undefined, tag: stTag || undefined, sources: stSources, limit: parseInt(stLimit) || 20 } satisfies StreamWidgetConfig,
        x: 0, y: 999, w: 4, h: 5, minW: 3, minH: 3,
      };
    } else if (addKind === 'opensearch-metric') {
      widget = {
        id, kind: 'opensearch-metric',
        config: { metric: osMetric, label: osLabel.trim() ? osLabel.trim().toUpperCase() : undefined, color: osColor } satisfies OpenSearchMetricConfig,
        x: 0, y: 999, w: 2, h: 2, minW: 2, minH: 2,
      };
    } else {
      const SIZE: Record<WidgetKind, { w: number; h: number; minW: number; minH: number }> = {
        'stat-counter':      { w: 2, h: 2, minW: 2, minH: 2 },
        'custom-counter':    { w: 2, h: 2, minW: 2, minH: 2 },
        'script-metric':     { w: 2, h: 2, minW: 2, minH: 2 },
        'http-metric':       { w: 2, h: 2, minW: 2, minH: 2 },
        'calendar':          { w: 4, h: 4, minW: 3, minH: 3 },
        'tasks':             { w: 4, h: 5, minW: 3, minH: 3 },
        'sessions-mini':     { w: 3, h: 4, minW: 2, minH: 3 },
        'notes':             { w: 3, h: 4, minW: 2, minH: 2 },
        'dispatch-form':     { w: 6, h: 4, minW: 4, minH: 3 },
        'action-items':      { w: 3, h: 4, minW: 2, minH: 2 },
        'resource-monitor':  { w: 12, h: 4, minW: 4, minH: 3 },
        'stream':            { w: 4, h: 5, minW: 3, minH: 3 },
        'opensearch-metric': { w: 2, h: 2, minW: 2, minH: 2 },
      };
      widget = { id, kind: addKind, config: {}, x: 0, y: 999, ...SIZE[addKind] };
    }
    onAdd(widget);
  }

  // --- edit state ---
  let editingWidget = $state<WidgetInstance | null>(null);

  function resetFormFields() {
    scLabel = ''; scColor = 'default'; scNavTarget = ''; scDataKey = 'activeSessions';
    ccLabel = ''; ccApi = 'hub_tasks'; ccStatus = ''; ccColor = '';
    smLabel = ''; smCommand = ''; smValueType = 'number'; smColor = ''; smInterval = '60';
    hmLabel = ''; hmUrl = ''; hmPath = ''; hmHeader = ''; hmValueType = 'number'; hmColor = ''; hmInterval = '60';
    stLabel = 'stream'; stProfile = ''; stTag = ''; stLimit = '20'; stSources = ['task', 'event'];
    osMetric = 'cost-today'; osLabel = ''; osColor = 'default';
  }

  function startEdit(w: WidgetInstance) {
    editingWidget = w;
    resetFormFields();
    const c = w.config as any;
    if (w.kind === 'script-metric') {
      smLabel = c.label ?? ''; smCommand = c.command ?? ''; smValueType = c.valueType ?? 'number'; smColor = c.color ?? ''; smInterval = String(c.interval ?? 60);
    } else if (w.kind === 'http-metric') {
      hmLabel = c.label ?? ''; hmUrl = c.url ?? ''; hmPath = c.path ?? ''; hmHeader = c.header ?? ''; hmValueType = c.valueType ?? 'number'; hmColor = c.color ?? ''; hmInterval = String(c.interval ?? 60);
    } else if (w.kind === 'stat-counter') {
      scLabel = c.label ?? ''; scColor = c.color ?? 'default'; scDataKey = c.dataKey ?? 'activeSessions'; scNavTarget = c.navTarget ?? '';
    } else if (w.kind === 'custom-counter') {
      ccLabel = c.label ?? ''; ccApi = c.api ?? 'hub_tasks'; ccStatus = c.filter?.status ?? ''; ccColor = c.color ?? '';
    } else if (w.kind === 'stream') {
      stLabel = c.label ?? 'stream'; stProfile = c.profile ?? ''; stTag = c.tag ?? ''; stLimit = String(c.limit ?? 20); stSources = c.sources ?? ['task', 'event'];
    } else if (w.kind === 'opensearch-metric') {
      osMetric = c.metric ?? 'cost-today'; osLabel = c.label ?? ''; osColor = c.color ?? 'default';
    }
  }

  function handleSaveEdit() {
    if (!editingWidget) return;
    const w = editingWidget;
    let config: any;
    if (w.kind === 'script-metric') {
      config = { label: metricLabel(smLabel, 'METRIC'), command: smCommand, valueType: smValueType, color: smColor || undefined, interval: parseInt(smInterval) || 60 };
    } else if (w.kind === 'http-metric') {
      config = { label: metricLabel(hmLabel, 'METRIC'), url: hmUrl, path: hmPath || undefined, header: hmHeader || undefined, valueType: hmValueType, color: hmColor || undefined, interval: parseInt(hmInterval) || 60 };
    } else if (w.kind === 'stat-counter') {
      config = { label: metricLabel(scLabel, 'COUNTER'), color: scColor, navTarget: scNavTarget || undefined, dataKey: scDataKey };
    } else if (w.kind === 'custom-counter') {
      config = { label: metricLabel(ccLabel, 'COUNT'), api: ccApi, filter: ccStatus ? { status: ccStatus } : {}, color: ccColor || undefined };
    } else if (w.kind === 'stream') {
      config = { label: stLabel || 'stream', profile: stProfile || undefined, tag: stTag || undefined, sources: stSources, limit: parseInt(stLimit) || 20 };
    } else if (w.kind === 'opensearch-metric') {
      config = { metric: osMetric, label: osLabel.trim() ? osLabel.trim().toUpperCase() : undefined, color: osColor };
    } else {
      config = w.config;
    }
    onUpdate({ ...w, config });
    editingWidget = null;
  }

  const KIND_LABELS: Record<WidgetKind, string> = {
    'stat-counter':      'Stat Counter',
    'custom-counter':    'Custom Counter',
    'script-metric':     'Script Metric',
    'http-metric':       'HTTP Metric',
    'calendar':          'Calendar',
    'tasks':             'Tasks',
    'sessions-mini':     'Live Sessions',
    'notes':             'Scratchpad',
    'dispatch-form':     'Dispatch Form',
    'action-items':      'Action Items',
    'resource-monitor':  'Resource Monitor',
    'stream':            'Stream',
    'opensearch-metric': 'OpenSearch Metric',
  };
</script>

{#if open}
  <!-- backdrop -->
  <div class="backdrop" onclick={onClose} aria-hidden="true"></div>

  <div class="drawer" role="dialog" aria-label="Widget settings" bind:this={drawerEl}>
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
              <label>Label
                <input class="metric-label" type="text" bind:value={scLabel} placeholder="MY COUNTER" autocorrect="off" autocapitalize="characters" spellcheck="false" autocomplete="off" />
              </label>
            </div>
            <div class="field">
              <label>Data
                <select bind:value={scDataKey}>
                {#each DATA_KEYS as d (d.key)}
                  <option value={d.key}>{d.label}</option>
                {/each}
              </select>
              </label>
            </div>
            <div class="field">
              <label>Color
                <select bind:value={scColor}>
                {#each COLORS as c (c.val)}
                  <option value={c.val}>{c.label}</option>
                {/each}
              </select>
              </label>
            </div>
            <div class="field">
              <label>Navigate to
                <select bind:value={scNavTarget}>
                {#each NAV_TARGETS as t (t)}
                  <option value={t}>{t || '(none)'}</option>
                {/each}
              </select>
              </label>
            </div>
          {:else if addKind === 'custom-counter'}
            <div class="field">
              <label>Label
                <input class="metric-label" type="text" bind:value={ccLabel} placeholder="MY METRIC" autocorrect="off" autocapitalize="characters" spellcheck="false" autocomplete="off" />
              </label>
            </div>
            <div class="field">
              <label>API
                <select bind:value={ccApi}>
                <option value="hub_tasks">Hub Tasks</option>
                <option value="sessions">Sessions</option>
              </select>
              </label>
            </div>
            <div class="field">
              <label>Status filter
                <input type="text" bind:value={ccStatus} placeholder="running, failed, … (optional)" />
              </label>
            </div>
            <div class="field">
              <label>Color (CSS)
                <input type="text" bind:value={ccColor} placeholder="#22c55e or var(--color-success)" />
              </label>
            </div>
          {:else if addKind === 'script-metric'}
            <div class="field">
              <label>Label
                <input class="metric-label" type="text" bind:value={smLabel} placeholder="UNREAD EMAILS" autocorrect="off" autocapitalize="characters" spellcheck="false" autocomplete="off" />
              </label>
            </div>
            <div class="field">
              <label>Command
                <textarea bind:value={smCommand} rows="3" placeholder="echo 42&#10;# or: echo 'hello world'"></textarea>
              </label>
            </div>
            <div class="field">
              <label>Value type
                <select bind:value={smValueType}>
                <option value="number">Number</option>
                <option value="string">String</option>
              </select>
              </label>
            </div>
            <div class="field">
              <label>Color (CSS)
                <input type="text" bind:value={smColor} placeholder="#22c55e or var(--color-success)" />
              </label>
            </div>
            <div class="field">
              <label>Interval (seconds)
                <input type="number" bind:value={smInterval} min="10" max="3600" />
              </label>
            </div>
          {:else if addKind === 'http-metric'}
            <div class="field">
              <label>Label
                <input class="metric-label" type="text" bind:value={hmLabel} placeholder="OPEN PRS" autocorrect="off" autocapitalize="characters" spellcheck="false" autocomplete="off" />
              </label>
            </div>
            <div class="field">
              <label>URL
                <input type="text" bind:value={hmUrl} placeholder="https://api.example.com/count" />
              </label>
            </div>
            <div class="field">
              <label>JSON path
                <input type="text" bind:value={hmPath} placeholder="data.total (leave blank for root)" />
              </label>
            </div>
            <div class="field">
              <label>Header (optional)
                <input type="text" bind:value={hmHeader} placeholder="Authorization: Bearer TOKEN" />
              </label>
            </div>
            <div class="field">
              <label>Value type
                <select bind:value={hmValueType}>
                <option value="number">Number</option>
                <option value="string">String</option>
              </select>
              </label>
            </div>
            <div class="field">
              <label>Color (CSS)
                <input type="text" bind:value={hmColor} placeholder="#22c55e or var(--color-info)" />
              </label>
            </div>
            <div class="field">
              <label>Interval (seconds)
                <input type="number" bind:value={hmInterval} min="10" max="3600" />
              </label>
            </div>
          {:else if addKind === 'stream'}
            <div class="field">
              <label>Label
                <input type="text" bind:value={stLabel} placeholder="stream" />
              </label>
            </div>
            <div class="field">
              <label>Profile (leave blank for all)
                <input type="text" bind:value={stProfile} placeholder="personal" />
              </label>
            </div>
            <div class="field">
              <label>Tag filter (optional)
                <input type="text" bind:value={stTag} placeholder="calendar" />
              </label>
            </div>
            <div class="field">
              <span class="group-label">Sources</span>
              <div class="check-row">
                <label class="check-label"><input type="checkbox" checked={stSources.includes('event')} onchange={e => { if ((e.target as HTMLInputElement).checked) { stSources = [...stSources, 'event']; } else { stSources = stSources.filter(s => s !== 'event'); } }} /> Events</label>
                <label class="check-label"><input type="checkbox" checked={stSources.includes('task')} onchange={e => { if ((e.target as HTMLInputElement).checked) { stSources = [...stSources, 'task']; } else { stSources = stSources.filter(s => s !== 'task'); } }} /> Tasks</label>
              </div>
            </div>
            <div class="field">
              <label>Max items
                <input type="number" bind:value={stLimit} min="5" max="100" />
              </label>
            </div>
          {:else if addKind === 'opensearch-metric'}
            <div class="field">
              <label>Metric
                <select bind:value={osMetric}>
                {#each OS_METRICS as m}
                  <option value={m.val}>{m.label}</option>
                {/each}
              </select>
              </label>
            </div>
            <div class="field">
              <label>Label (optional)
                <input class="metric-label" type="text" bind:value={osLabel} placeholder="leave blank for default" autocorrect="off" autocapitalize="characters" spellcheck="false" autocomplete="off" />
              </label>
            </div>
            <div class="field">
              <label>Color
                <select bind:value={osColor}>
                {#each COLORS as c}
                  <option value={c.val}>{c.label}</option>
                {/each}
              </select>
              </label>
            </div>
          {:else}
            <p class="no-config">No configuration needed.</p>
          {/if}

          <button class="btn-add" onclick={handleAdd}>Add widget</button>
        </div>

      {:else}
        <!-- Manage tab -->
        {#if editingWidget}
          <div class="edit-header">
            <button class="btn-back" onclick={() => editingWidget = null}>← back</button>
            <span class="edit-title">Edit {KIND_LABELS[editingWidget.kind]}</span>
          </div>

          <div class="config-form">
            {#if editingWidget.kind === 'script-metric'}
              <div class="field"><label>Label <input type="text" bind:value={smLabel} placeholder="UNREAD EMAILS" /></label></div>
              <div class="field"><label>Command <textarea bind:value={smCommand} rows="3" placeholder="echo 42"></textarea></label></div>
              <div class="field"><label>Value type <select bind:value={smValueType}><option value="number">Number</option><option value="string">String</option></select></label></div>
              <div class="field"><label>Color (CSS) <input type="text" bind:value={smColor} placeholder="#22c55e" /></label></div>
              <div class="field"><label>Interval (seconds) <input type="number" bind:value={smInterval} min="10" max="3600" /></label></div>
            {:else if editingWidget.kind === 'http-metric'}
              <div class="field"><label>Label <input type="text" bind:value={hmLabel} placeholder="OPEN PRS" /></label></div>
              <div class="field"><label>URL <input type="text" bind:value={hmUrl} placeholder="https://api.example.com/count" /></label></div>
              <div class="field"><label>JSON path <input type="text" bind:value={hmPath} placeholder="data.total" /></label></div>
              <div class="field"><label>Header (optional) <input type="text" bind:value={hmHeader} placeholder="Authorization: Bearer TOKEN" /></label></div>
              <div class="field"><label>Value type <select bind:value={hmValueType}><option value="number">Number</option><option value="string">String</option></select></label></div>
              <div class="field"><label>Color (CSS) <input type="text" bind:value={hmColor} /></label></div>
              <div class="field"><label>Interval (seconds) <input type="number" bind:value={hmInterval} min="10" max="3600" /></label></div>
            {:else if editingWidget.kind === 'stat-counter'}
              <div class="field"><label>Label <input type="text" bind:value={scLabel} placeholder="MY COUNTER" /></label></div>
              <div class="field"><label>Data <select bind:value={scDataKey}>{#each DATA_KEYS as d (d.key)}<option value={d.key}>{d.label}</option>{/each}</select></label></div>
              <div class="field"><label>Color <select bind:value={scColor}>{#each COLORS as c (c.val)}<option value={c.val}>{c.label}</option>{/each}</select></label></div>
              <div class="field"><label>Navigate to <select bind:value={scNavTarget}>{#each NAV_TARGETS as t (t)}<option value={t}>{t || '(none)'}</option>{/each}</select></label></div>
            {:else if editingWidget.kind === 'custom-counter'}
              <div class="field"><label>Label <input type="text" bind:value={ccLabel} placeholder="MY METRIC" /></label></div>
              <div class="field"><label>API <select bind:value={ccApi}><option value="hub_tasks">Hub Tasks</option><option value="sessions">Sessions</option><option value="repos">Repos</option></select></label></div>
              <div class="field"><label>Status filter <input type="text" bind:value={ccStatus} placeholder="running, failed, … (optional)" /></label></div>
              <div class="field"><label>Color (CSS) <input type="text" bind:value={ccColor} /></label></div>
            {:else if editingWidget.kind === 'stream'}
              <div class="field"><label>Label <input type="text" bind:value={stLabel} /></label></div>
              <div class="field"><label>Profile (blank = all) <input type="text" bind:value={stProfile} placeholder="personal" /></label></div>
              <div class="field"><label>Tag filter <input type="text" bind:value={stTag} placeholder="calendar" /></label></div>
              <div class="field">
                <span class="group-label">Sources</span>
                <div class="check-row">
                  <label class="check-label"><input type="checkbox" checked={stSources.includes('event')} onchange={e => { if ((e.target as HTMLInputElement).checked) { stSources = [...stSources, 'event']; } else { stSources = stSources.filter(s => s !== 'event'); } }} /> Events</label>
                  <label class="check-label"><input type="checkbox" checked={stSources.includes('task')} onchange={e => { if ((e.target as HTMLInputElement).checked) { stSources = [...stSources, 'task']; } else { stSources = stSources.filter(s => s !== 'task'); } }} /> Tasks</label>
                </div>
              </div>
              <div class="field"><label>Max items <input type="number" bind:value={stLimit} min="5" max="100" /></label></div>
            {:else if editingWidget.kind === 'opensearch-metric'}
              <div class="field"><label>Metric <select bind:value={osMetric}>{#each OS_METRICS as m (m.val)}<option value={m.val}>{m.label}</option>{/each}</select></label></div>
              <div class="field"><label>Label (optional) <input type="text" bind:value={osLabel} placeholder="leave blank for default" /></label></div>
              <div class="field"><label>Color <select bind:value={osColor}>{#each COLORS as c (c.val)}<option value={c.val}>{c.label}</option>{/each}</select></label></div>
            {:else}
              <p class="no-config">No editable configuration for this widget type.</p>
            {/if}
            <button class="btn-add" onclick={handleSaveEdit}>Save changes</button>
          </div>
        {:else}
          <div class="widget-list">
            {#each widgets as w (w.id)}
              <div class="widget-row">
                <div class="widget-info">
                  <span class="widget-kind">{KIND_LABELS[w.kind]}</span>
                  {#if 'label' in w.config}
                    <span class="widget-label">{(w.config as any).label}</span>
                  {/if}
                </div>
                <button class="btn-edit" onclick={() => startEdit(w)} aria-label="Edit widget">✎</button>
                <button class="btn-remove" onclick={() => onRemove(w.id)} aria-label="Remove widget">✕</button>
              </div>
            {/each}
            {#if widgets.length === 0}
              <p class="empty">No widgets.</p>
            {/if}
          </div>

          <button class="btn-reset" onclick={onReset}>Reset to default layout</button>
        {/if}
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

  .field label,
  .field .group-label {
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

  .field input.metric-label {
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .field input.metric-label::placeholder {
    text-transform: uppercase;
  }
  .field textarea { resize: vertical; font-family: var(--font-mono); }

  .no-config {
    font-size: 12px;
    color: var(--color-text-tertiary);
    margin: 0;
  }

  .check-row { display: flex; gap: var(--spacing-md); }
  .check-label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-text-secondary); cursor: pointer; }

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

  .edit-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
  }

  .btn-back {
    background: none;
    border: none;
    color: var(--color-accent);
    font-size: 12px;
    cursor: pointer;
    padding: 0;
    font-family: inherit;
  }
  .btn-back:hover { opacity: 0.8; }

  .edit-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-secondary);
  }

  .widget-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 12px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-primary);
    border-radius: var(--radius-md);
  }

  .widget-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .widget-kind {
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-primary);
  }

  .widget-label {
    font-size: 11px;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .btn-edit {
    background: none;
    border: none;
    color: var(--color-text-tertiary);
    cursor: pointer;
    font-size: 14px;
    padding: 2px 4px;
    flex-shrink: 0;
  }
  .btn-edit:hover { color: var(--color-accent); }

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
