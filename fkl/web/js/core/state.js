/**
 * Application state and a minimal pub/sub.
 *
 * Small enough that a framework would be more machinery than the problem
 * needs, but centralised so views never reach into each other.
 */

const listeners = new Set();

const state = {
  tab: 'relations',
  filters: { kind: '', search: '', docId: '' },
  stats: null,
};

export function getState() { return state; }

export function setState(patch) {
  Object.assign(state, patch);
  listeners.forEach((fn) => fn(state));
}

export function setFilter(patch) {
  state.filters = { ...state.filters, ...patch };
  listeners.forEach((fn) => fn(state));
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
