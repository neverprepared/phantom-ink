export type WidgetKind =
  | 'stat-counter'
  | 'dispatch-form'
  | 'action-items'
  | 'resource-monitor'
  | 'custom-counter'
  | 'script-metric'
  | 'http-metric'
  | 'stream'
  | 'calendar'
  | 'tasks'
  | 'notes'
  | 'sessions-mini'
  | 'opensearch-metric';

export type OpenSearchMetric =
  | 'cost-today'
  | 'tokens-today'
  | 'api-requests-1h'
  | 'avg-latency-1h';

export interface OpenSearchMetricConfig {
  metric: OpenSearchMetric;
  label?: string;
  color?: 'green' | 'blue' | 'red' | 'orange' | 'muted' | 'default';
}

export interface StatCounterConfig {
  label: string;
  color: 'green' | 'blue' | 'red' | 'orange' | 'muted' | 'default';
  navTarget?: string;
  dataKey:
    | 'activeSessions'
    | 'runningTasks'
    | 'failedTasks'
    | 'scheduledFires'
    | 'actionItems'
    | 'attentionItems'
    | 'offlineRunners'
    | 'peakQueue1h';
}

export interface CustomCounterConfig {
  label: string;
  api: 'sessions' | 'hub_tasks';
  filter: { status?: string };
  color?: string;
}

export interface ScriptMetricConfig {
  label: string;
  command: string;
  valueType?: 'number' | 'string';
  color?: string;
  interval?: number;
  jobId?: string;      // set after auto-registration with collect scheduler
}

export interface HttpMetricConfig {
  label: string;
  url: string;
  path?: string;
  header?: string;
  valueType?: 'number' | 'string';
  color?: string;
  interval?: number;
  jobId?: string;      // set after auto-registration with collect scheduler
}

export interface StreamWidgetConfig {
  label?: string;
  profile?: string;  // '' = all profiles
  tag?: string;      // '' = all tags
  sources?: ('task' | 'event')[];  // default: both
  limit?: number;    // max rows, default 20
}

export interface NotesWidgetConfig {
  widgetId?: string;
}

export type WidgetConfig = StatCounterConfig | CustomCounterConfig | ScriptMetricConfig | HttpMetricConfig | StreamWidgetConfig | NotesWidgetConfig | OpenSearchMetricConfig | Record<string, never>;

export interface WidgetInstance {
  id: string;
  kind: WidgetKind;
  config: WidgetConfig;
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
  collectionId?: string;
}

export interface DashboardCollection {
  id: string;
  name: string;
}

export interface DashboardLayout {
  version: 1;
  collections?: DashboardCollection[];
  activeCollectionId?: string;
  widgets: WidgetInstance[];
}

export interface ActionItem {
  kind: string;
  title: string;
  desc: string;
  severity: 'urgent' | 'warning' | 'info';
  ref?: string;
  /** Optional navigation target panel id. Defaults to 'stream'. */
  navTarget?: string;
  /** When set, clicking the row calls AttentionOpenTarget(refId) instead of
   *  a simple panel switch. Lets bus-attention items drill into their owner. */
  openAttentionId?: string;
}

export interface SessionSummary {
  name: string;
  status: string;
  workspace_profile?: string;
  [key: string]: unknown;
}

export interface HubTask {
  id: string;
  description: string;
  status: string;
  workspace_profile?: string;
  spawned_by?: string;
  job_id?: string;
  [key: string]: unknown;
}

export interface UpcomingFire {
  schedule_id: string;
  loop_id: string;
  loop_name: string;
  cron_expr: string;
  next_fire_at: string;
}

export interface DockerStat {
  name: string;
  id: string;
  cpu_perc: string;
  mem_usage: string;
  mem_perc: string;
  net_io: string;
  block_io: string;
  pids: string;
}

export interface LocalProcess {
  pid: number;
  name: string;
  cpu: number;
  mem: number;
  [key: string]: unknown;
}

export interface OpenSearchOverview {
  cost_today_usd: number;
  tokens_today: number;
  api_requests_1h: number;
  avg_latency_ms_1h: number;
  as_of: string;
  workspace: string;
  matched_workspace: boolean;
}

export interface DashboardData {
  sessions: SessionSummary[];
  hubTasks: HubTask[];
  fires: UpcomingFire[];
  taskStats: { pending: number; running: number; succeeded: number; failed: number; cancelled: number } | null;
  dockerStats: DockerStat[];
  localProcs: LocalProcess[];
  systemInfo: { cpu_cores: number; mem_total_gib: number };
  actionItems: ActionItem[];
  activeSessions: number;
  runningTasks: number;
  failedTasks: number;
  attentionItems: number;
  offlineRunners: number;
  peakQueue1h: number;
  loading: boolean;
  refreshing: boolean;
  opensearch?: OpenSearchOverview | null;
}
