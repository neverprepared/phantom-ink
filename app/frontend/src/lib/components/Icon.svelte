<script lang="ts">
  export const PATHS: Record<string, string> = {
    dashboard:    '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="4" rx="1.5"/><rect x="14" y="10" width="7" height="11" rx="1.5"/><rect x="3" y="13" width="7" height="8" rx="1.5"/>',
    stream:       '<line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/><circle cx="9" cy="7" r="2" fill="var(--bg-elev)"/><circle cx="15" cy="12" r="2" fill="var(--bg-elev)"/><circle cx="8" cy="17" r="2" fill="var(--bg-elev)"/>',
    cube:         '<path d="M12 2.5 21 7v10l-9 4.5L3 17V7z"/><path d="M3 7l9 4.5L21 7"/><line x1="12" y1="11.5" x2="12" y2="21.5"/>',
    server:       '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><circle cx="7" cy="7.5" r="1" fill="currentColor" stroke="none"/><circle cx="7" cy="16.5" r="1" fill="currentColor" stroke="none"/>',
    // Repeat-style loop: two arrows wrapping a horizontal cycle.
    // Reads "iterate" at a glance, distinct from `link` (chain — two
    // interlocking arcs) and `refresh` (single curved arrow).
    loops:       '<polyline points="17 4 20 7 17 10" fill="none"/><path d="M4 11V9a3 3 0 0 1 3-3h13"/><polyline points="7 20 4 17 7 14" fill="none"/><path d="M20 13v2a3 3 0 0 1-3 3H4"/>',
    chat:         '<path d="M4 5.5h16v11H9l-4 3.5V16.5H4z" stroke-linejoin="round"/>',
    check:        '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 3 3 5-6"/>',
    gear:         '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v2.4M12 19.1v2.4M21.5 12h-2.4M4.9 12H2.5M18.7 5.3l-1.7 1.7M7 17l-1.7 1.7M18.7 18.7 17 17M7 7 5.3 5.3"/>',
    person:       '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    search:       '<circle cx="11" cy="11" r="6.5"/><line x1="21" y1="21" x2="16" y2="16"/>',
    refresh:      '<path d="M20 11a8 8 0 1 0-.6 4"/><path d="M20 5v6h-6"/>',
    chevL:        '<path d="m14 6-6 6 6 6"/>',
    chevR:        '<path d="m9 6 6 6-6 6"/>',
    chevD:        '<path d="m6 9 6 6 6-6"/>',
    plus:         '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    x:            '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
    calendar:     '<rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/>',
    pulse:        '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
    bolt:         '<path d="M13 2 4 14h6l-1 8 9-12h-6z" stroke-linejoin="round"/>',
    spark:        '<path d="M3 17l5-5 4 3 6-8"/><circle cx="18" cy="7" r="1.4" fill="currentColor" stroke="none"/>',
    note:         '<path d="M5 3h10l4 4v14H5z"/><path d="M15 3v4h4"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="13" y2="16"/>',
    lock:         '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    terminal:     '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3"/><line x1="12.5" y1="15" x2="16" y2="15"/>',
    play:         '<path d="M7 5l11 7-11 7z" fill="currentColor" stroke="none"/>',
    stop:         '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
    link:         '<path d="M9.5 14.5 14.5 9.5"/><path d="M7 17a3.5 3.5 0 0 1 0-5l2-2"/><path d="M17 7a3.5 3.5 0 0 1 0 5l-2 2"/>',
    trash:        '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>',
    monitor:      '<rect x="3" y="4" width="18" height="12" rx="2"/><line x1="8" y1="20" x2="16" y2="20"/><line x1="12" y1="16" x2="12" y2="20"/>',
    drag:         '<circle cx="9" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="18" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.4" fill="currentColor" stroke="none"/>',
    moon:         '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
    sun:          '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/>',
    cloud:        '<path d="M7 18a4 4 0 0 1 .5-8 5.5 5.5 0 0 1 10.5 2 3.5 3.5 0 0 1-.5 6z"/>',
    eye:          '<path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/>',
    paper:        '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    auto:         '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"/>',
    arrowUpRight: '<path d="M7 17 17 7M9 7h8v8"/>',
    filter:       '<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
    pin:          '<path d="M9 3h6l-1 6 3 3v2h-4v5l-1 2-1-2v-5H6v-2l3-3z"/>',
  };

  let {
    name,
    size = 20,
    stroke = 1.8,
    style = '',
    class: className = '',
  }: {
    name: string;
    size?: number;
    stroke?: number;
    style?: string;
    class?: string;
  } = $props();

  const d = $derived(PATHS[name] ?? PATHS.cube);
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width={stroke}
  stroke-linecap="round"
  class={className}
  style={style}
>
  {@html d}
</svg>
