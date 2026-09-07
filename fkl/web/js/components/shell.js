/** Top bar, metric strip and tab bar — the parts present in every view. */

import { html } from '../core/dom.js';
import { theme } from '../core/theme.js';
import { metricTile } from './primitives.js';

export const TABS = [
  { id: 'relations', label: 'Relationships' },
  { id: 'claims', label: 'Claims' },
  { id: 'review', label: 'Needs review' },
  { id: 'documents', label: 'Documents' },
];

export function engineBadge(engine = {}) {
  const selfContained = !engine.requires_api_key;
  return html`
    <div class="engine ${selfContained ? '' : 'engine--warn'}"
         title="Extraction engine currently in use">
      <span class="engine__dot"></span>
      ${engine.extractor || '—'}${selfContained ? ' · no API key' : ' · needs API key'}
    </div>`;
}

export const themeButton = () => html`
  <button class="btn btn--ghost btn--icon" id="theme-toggle" type="button"
          title="Theme: ${theme.current} (click to change)"
          aria-label="Colour theme: ${theme.current}">${theme.glyph()}</button>`;

/**
 * Headline figures. The three verdict counts are tinted to match the cards
 * below, so the strip doubles as a legend rather than seven identical boxes.
 */
export function metrics(stats) {
  const kinds = stats.relations_by_kind || {};
  return html`<div class="metrics">
    ${metricTile('Documents', stats.documents ?? 0)}
    ${metricTile('Claims', stats.claims ?? 0)}
    ${metricTile('From tables', stats.claims_from_tables ?? 0)}
    ${metricTile('Corroborations', kinds.corroboration ?? 0, 'affirm')}
    ${metricTile('Contradictions', kinds.contradiction ?? 0, 'conflict')}
    ${metricTile('Reconciled', kinds.reconciled ?? 0, 'explain')}
    ${metricTile('Quarantined', stats.claims_quarantined ?? 0, 'unknown')}
  </div>`;
}

export function tabs(activeTab, stats = {}) {
  const kinds = stats.relations_by_kind || {};
  const counts = {
    relations: Object.entries(kinds)
      .filter(([kind]) => kind !== 'unrelated')
      .reduce((total, [, n]) => total + n, 0),
    claims: stats.claims,
    review: stats.claims_needing_review,
    documents: stats.documents,
  };
  return html`<nav class="tabs" role="tablist" aria-label="Views">${TABS.map((tab) => html`
    <button class="tab" type="button" role="tab" data-tab="${tab.id}"
            aria-selected="${activeTab === tab.id ? 'true' : 'false'}">
      ${tab.label}${counts[tab.id] !== undefined
        ? html`<span class="tab__count">${counts[tab.id]}</span>` : ''}
    </button>`)}</nav>`;
}
