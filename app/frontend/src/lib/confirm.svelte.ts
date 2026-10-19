/**
 * In-app confirmation dialog.
 *
 * Replaces window.confirm(), which is an unreliable no-op in the Wails webview:
 * it returns undefined, so the usual `if (!window.confirm(msg)) return;` guard
 * bails unconditionally and the action silently never runs. That had already
 * been discovered twice in this codebase (TokensPanel, LoopsPanel) and worked
 * around locally with two-step inline buttons, while four destructive actions
 * elsewhere stayed broken.
 *
 * confirmAsk() is a drop-in for window.confirm() — it returns a promise of the
 * answer, so a call site changes only by awaiting it. Unlike a two-step button
 * it keeps the question text, which for some of these actions carries the
 * difference between "move to trash" and "permanently delete".
 *
 * Requires <ConfirmHost /> to be mounted once, at the app root.
 */

export interface ConfirmRequest {
  id: number;
  message: string;
  /** Title line; defaults to a generic "Confirm". */
  title: string;
  /** Label for the affirmative button. */
  confirmLabel: string;
  /** Styles the affirmative button as destructive. */
  danger: boolean;
  resolve: (ok: boolean) => void;
}

export interface ConfirmOptions {
  title?: string;
  confirmLabel?: string;
  danger?: boolean;
}

let _pending = $state<ConfirmRequest | null>(null);
let _idCounter = 0;

/**
 * Ask the user to confirm. Resolves true if they confirm, false otherwise
 * (cancel, Escape, or backdrop click).
 *
 * If a confirmation is somehow already open, the new request is auto-declined
 * rather than queued or silently dropped: two destructive prompts racing means
 * a bug at the call site, and declining is the safe answer.
 */
export function confirmAsk(message: string, opts: ConfirmOptions = {}): Promise<boolean> {
  if (_pending) return Promise.resolve(false);
  return new Promise<boolean>((resolve) => {
    _pending = {
      id: ++_idCounter,
      message,
      title: opts.title ?? 'Confirm',
      confirmLabel: opts.confirmLabel ?? 'Confirm',
      danger: opts.danger ?? true,
      resolve,
    };
  });
}

export const confirmDialog = {
  get pending() {
    return _pending;
  },

  /** Answer the open request. No-op if nothing is open. */
  answer(ok: boolean) {
    const req = _pending;
    if (!req) return;
    // Clear first, so a resolver that immediately opens another confirmation
    // is not rejected by the single-request guard above.
    _pending = null;
    req.resolve(ok);
  },
};
