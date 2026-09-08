/**
 * Small collection of data-fetching hooks tailored to the FKL API.
 *
 * Each returns `{ data, loading, error, refresh }`. Where useful, they
 * accept a polling interval so a view can keep itself in sync without
 * setting up its own timer.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';

/** Generic async fetch with dep-array invalidation. */
export function useAsync(fn, deps = []) {
  // Initialise `data` as undefined (not null) so consumers can rely on
  // destructuring defaults like `const { data: rows = [] } = useAsync(...)`.
  // JS default-value assignment only fills in for `undefined`.
  const [state, setState] = useState({ data: undefined, loading: true, error: null });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fn();
      setState({ data, loading: false, error: null });
    } catch (error) {
      setState({ data: undefined, loading: false, error });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { load(); }, [load]);
  return { ...state, refresh: load };
}

/** Poll a value every `intervalMs` while the component is mounted. */
export function usePoll(fn, intervalMs, deps = []) {
  const result = useAsync(fn, deps);
  const stableRefresh = useRef(result.refresh);
  stableRefresh.current = result.refresh;

  useEffect(() => {
    if (!intervalMs) return undefined;
    const id = setInterval(() => stableRefresh.current(), intervalMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);

  return result;
}

/** Shared stats hook — polls every 10 s. */
export const useStats = () => usePoll(() => api.stats(), 10000);
export const useDocuments = () => useAsync(() => api.documents(), []);
export const useJobs = (fast) => usePoll(() => api.jobs(), fast ? 1500 : 5000);
export const useRelations = (filters) => useAsync(
  () => api.relations(filters), [JSON.stringify(filters)],
);
export const useClaims = (filters) => useAsync(
  () => api.claims(filters), [JSON.stringify(filters)],
);
