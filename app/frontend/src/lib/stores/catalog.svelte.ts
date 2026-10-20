/**
 * Near-static catalogs, read once for the whole app.
 *
 * The agent-role catalog had FIVE independent consumers, each repeating the
 * same fetch and the same `.map(r => r.name).filter(Boolean)`:
 * SessionsPanel, JobsPanel, CodeStore, RuleForm and TodoActions. It is a
 * catalog — it changes when someone authors a role, not per interaction — so
 * five reads of it is four too many, and every one of them also had its own
 * copy of the fallback behaviour.
 *
 * These live apart from the fleet store because they are not fleet state and
 * are not polled: the shared poll re-reads liveness, while a catalog is read
 * on demand and refreshed when something writes to it.
 */
import { resource } from './resource.svelte';

/** The role catalog as the backend returns it. Consumers usually want names. */
export const agentRolesRes = resource<any[]>(
  async (api) => ((await api.ListAgentRoles()) ?? []) as any[],
  { initial: [], label: 'Failed to load agent roles' },
);

/**
 * Role names, with the fallback every call site had its own copy of.
 *
 * `fallback` is the caller's own default because they differ: the dispatch
 * lanes default to 'worker', the session form to 'assistant'. Passing it in
 * keeps one fetch without flattening that difference.
 */
export function agentRoleNames(fallback: string[]): string[] {
  const names = agentRolesRes.value.map((r: any) => r?.name).filter(Boolean) as string[];
  return names.length ? names : fallback;
}

/** Load the catalog unless it is already held. Safe to call from every mount. */
export async function ensureAgentRoles(): Promise<void> {
  if (agentRolesRes.loaded) return;
  await agentRolesRes.refresh();
}
