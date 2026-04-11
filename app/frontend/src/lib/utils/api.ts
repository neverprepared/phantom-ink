/** Shared lazy-loading API accessor for Wails Go bindings. */

let api: any = null;

export async function getApi() {
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
