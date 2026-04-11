/** Toast notification system. */

export interface Notification {
  id: number;
  type: 'success' | 'error' | 'info' | 'warning';
  message: string;
}

let _notifications = $state<Notification[]>([]);
let _idCounter = 0;

export const notifications = {
  get value() { return _notifications; },

  success(message: string, duration = 3000) {
    return this._add('success', message, duration);
  },

  error(message: string, duration = 5000) {
    return this._add('error', message, duration);
  },

  info(message: string, duration = 3000) {
    return this._add('info', message, duration);
  },

  warning(message: string, duration = 4000) {
    return this._add('warning', message, duration);
  },

  _add(type: Notification['type'], message: string, duration: number) {
    const id = ++_idCounter;
    _notifications = [..._notifications, { id, type, message }];
    if (duration > 0) {
      setTimeout(() => this.dismiss(id), duration);
    }
    return id;
  },

  dismiss(id: number) {
    _notifications = _notifications.filter(n => n.id !== id);
  },

  clear() {
    _notifications = [];
  },
};
