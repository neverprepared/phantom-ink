/** Shared lazy-loading API accessor for Wails Go bindings. */

type AppBindings = typeof import('../../../wailsjs/go/main/App');

let api: AppBindings | null = null;

export async function getApi(): Promise<AppBindings | null> {
  if (api) return api;
  try {
    api = await import('../../../wailsjs/go/main/App');
  } catch {
    api = null;
  }
  return api;
}

/** Open a URL in the system browser via Wails runtime. */
export function openInBrowser(url: string) {
  (window as any).runtime?.BrowserOpenURL(url);
}

/**
 * Wrap an API-call promise with a fallback so one failure doesn't reject a
 * Promise.all batch — but surface the error to the console instead of
 * swallowing it silently. Replaces the bare `.catch(() => [])` swallows that
 * made panels render false-empty on a fetch failure with no trace. Background
 * polls stay quiet in the UI (no toast spam); the failure is visible in devtools.
 */
export function safe<T, F = T>(p: Promise<T>, fallback: F, label?: string): Promise<T | F> {
  return p.catch((err) => {
    console.warn(`[api] ${label ?? 'request'} failed:`, err);
    return fallback;
  });
}
