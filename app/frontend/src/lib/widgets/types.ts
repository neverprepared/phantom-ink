export type WidgetKind =
  | 'stat-counter'
  | 'dispatch-form'
  | 'chains-list'
  | 'action-items'
  | 'resource-monitor'
  | 'custom-counter'
  | 'script-metric'
  | 'http-metric';

export interface StatCounterConfig {
  label: string;
  color: 'green' | 'blue' | 'red' | 'orange' | 'muted' | 'default';
  navTarget?: string;
  dataKey: 'activeSessions' | 'runningTasks' | 'failedTasks' | 'scheduledFires' | 'actionItems';
}

export interface CustomCounterConfig {
  label: string;
  api: 'sessions' | 'hub_tasks' | 'repos';
  filter: { status?: string };
  color?: string;
}

export interface ScriptMetricConfig {
  label: string;
  command: string;
  valueType?: 'number' | 'string';
  color?: string;
  interval?: number;
}

export interface HttpMetricConfig {
  label: string;
  url: string;
  path?: string;
  header?: string;
  valueType?: 'number' | 'string';
  color?: string;
  interval?: number;
}

export type WidgetConfig = StatCounterConfig | CustomCounterConfig | ScriptMetricConfig | HttpMetricConfig | Record<string, never>;

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
}

export interface DashboardLayout {
  version: 1;
  widgets: WidgetInstance[];
}

export interface ActionItem {
  kind: string;
  title: string;
  desc: string;
  severity: 'urgent' | 'warning' | 'info';
  ref?: string;
}

export interface DashboardData {
  sessions: any[];
  hubTasks: any[];
  fires: any[];
  taskStats: any | null;
  dockerStats: any[];
  localProcs: any[];
  systemInfo: { cpu_cores: number; mem_total_gib: number };
  actionItems: ActionItem[];
  activeSessions: number;
  runningTasks: number;
  failedTasks: number;
  loading: boolean;
  refreshing: boolean;
}
