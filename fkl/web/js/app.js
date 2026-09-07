/**
 * Application entry point.
 *
 * Responsibilities kept deliberately narrow: load shared stats, pick a view,
 * render it, and route user intent back into state. Views know nothing about
 * each other and nothing about the DOM outside their own container.
 */

import { api } from './core/api.js';
import { delegate, html, qs, render } from './core/dom.js';
import { getState, setFilter, setState } from './core/state.js';
import { theme } from './core/theme.js';
import { engineBadge, metrics, tabs, themeButton, uploadButton } from './components/shell.js';
import { errorState, skeleton } from './components/primitives.js';
import { bindGlobalDrop, dropOverlay, openFilePicker } from './components/uploader.js';

import * as relationsView from './views/relations.js';
import * as claimsView from './views/claims.js';
import * as reviewView from './views/review.js';
import * as documentsView from './views/documents.js';

const VIEWS = {
  relations: relationsView,
  claims: claimsView,
  review: reviewView,
  documents: documentsView,
};

const STATS_REFRESH_MS = 10000;
const JOB_POLL_MS = 2000;

let pollTimer = null;

/* ---------------------------------------------------------------- header */
async function refreshShell() {
  const stats = await api.stats();
  setState({ stats });
  render('#engine-slot', engineBadge(stats.engine));
  render('#metrics-slot', metrics(stats));
  render('#tabs-slot', tabs(getState().tab, stats));
  return stats;
}

/* ------------------------------------------------------------------ view */
async function refreshView() {
  const state = getState();
  const view = VIEWS[state.tab] || VIEWS.relations;
  const container = qs('#view');

  render(container, skeleton(2));
  try {
    render(container, await view.render(state));
    view.mount?.(container, { refresh: refreshView });
    schedulePolling(view);
  } catch (error) {
    render(container, errorState(error.message));
  }
}

/** The documents view polls while an ingest job is running; nothing else does. */
function schedulePolling(view) {
  clearTimeout(pollTimer);
  if (!view.isBusy) return;
  pollTimer = setTimeout(async () => {
    if (getState().tab !== 'documents') return;
    if (await view.isBusy()) refreshView();
  }, JOB_POLL_MS);
}

async function refreshAll() {
  await refreshShell().catch(() => {});
  await refreshView();
}

/* ---------------------------------------------------------------- events */
function bindGlobalHandlers() {
  delegate(document, 'click', '[data-tab]', (_event, el) => {
    if (getState().tab === el.dataset.tab) return;
    setState({ tab: el.dataset.tab });
    render('#tabs-slot', tabs(el.dataset.tab, getState().stats || {}));
    refreshView();
  });

  delegate('#view', 'click', '[data-kind]', (_event, el) => {
    setFilter({ kind: el.dataset.kind });
    refreshView();
  });

  delegate('#view', 'click', '#rebuild-relations', async (_event, el) => {
    el.disabled = true;
    el.textContent = 'Rebuilding…';
    await api.rebuildRelations().catch(() => {});
    setTimeout(refreshAll, 3500);
  });

  delegate('#view', 'click', '[data-delete]', async (_event, el) => {
    await api.deleteDocument(el.dataset.delete).catch(() => {});
    refreshAll();
  });

  delegate('#view', 'keydown', '#claim-search', (event, el) => {
    if (event.key !== 'Enter') return;
    setFilter({ search: el.value });
    refreshView();
  });

  delegate('#view', 'change', '#document-filter', (_event, el) => {
    setFilter({ docId: el.value });
    refreshView();
  });

  // Upload is reachable from every view. If the picker is not on screen
  // (any tab but Documents), go there first, then open it.
  delegate(document, 'click', '#upload-trigger', async () => {
    if (getState().tab !== 'documents') {
      setState({ tab: 'documents' });
      render('#tabs-slot', tabs('documents', getState().stats || {}));
      await refreshView();
    }
    openFilePicker();
  });

  delegate(document, 'click', '#theme-toggle', (_event, el) => {
    const next = theme.cycle();
    el.textContent = theme.glyph(next);
    el.title = `Theme: ${next} (click to change)`;
  });
}

/* ------------------------------------------------------------------ boot */
function mountShell() {
  render('#topbar-right',
    html`<div id="engine-slot"></div>${uploadButton()}${themeButton()}`);
  render('#overlay-slot', dropOverlay());
}

async function start() {
  theme.init();
  mountShell();
  bindGlobalHandlers();
  bindGlobalDrop({ refresh: refreshAll });

  // An empty corpus opens on the upload panel: with nothing ingested, the
  // relationship view has nothing to say and adding a document is the only
  // useful action.
  const stats = await api.stats().catch(() => null);
  if (stats && !stats.documents) setState({ tab: 'documents' });

  await refreshAll();
  setInterval(() => {
    if (getState().tab !== 'documents') refreshShell().catch(() => {});
  }, STATS_REFRESH_MS);
}

start();
