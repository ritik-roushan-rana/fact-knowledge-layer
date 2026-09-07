/** Top bar, metric strip and tab bar — the parts present in every view. */

import { html } from '../core/dom.js';
import { theme } from '../core/theme.js';

export const TABS = [
  { id: 'relations', label: 'Relationships' },
  { id: 'claims', label: 'Claims' },
  { id: 'review', label: 'Needs review' },
  { id: 'documents', label: 'Documents' },
];

export function engineBadge(engine = {}) {
  const selfContained = !engine.requires_api_key;
  return html`
    <div class="engine ${selfContained ? '' : 'engine--warn'}">
      <span class="engine__dot"></span>
      ${engine.extractor || '—'} engine${selfContained ? ' · no API key required' : ''}
    </div>`;
}

export const themeButton = () => html`
  <button class="btn btn--ghost btn--icon" id="theme-toggle"
          title="Theme: ${theme.current} (click to change)"
          aria-label="Toggle colour theme">${theme.glyph()}</button>`;

export function metrics(stats) {
  const kinds = stats.relations_by_kind || {};
  const cells = [
    ['Documents', stats.documents, ''],
    ['Claims', stats.claims, ''],
    ['From tables', stats.claims_from_tables || 0, ''],
    ['Corroborations', kinds.corroboration || 0, 'ok'],
    ['Contradictions', kinds.contradiction || 0, 'bad'],
    ['Reconciled', kinds.reconciled || 0, 'violet'],
    ['Quarantined', stats.claims_quarantined || 0, 'amber'],
  ];
  return html`<div class="metrics">${cells.map(([label, value, tone]) => html`
    <div class="metric ${tone ? `metric--${tone}` : ''}">
      <div class="metric__value num">${value}</div>
      <div class="metric__label">${label}</div>
    </div>`)}</div>`;
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
  return html`<div class="tabs" role="tablist">${TABS.map((tab) => html`
    <button class="tab" role="tab" data-tab="${tab.id}"
            aria-selected="${activeTab === tab.id ? 'true' : 'false'}">${tab.label}${
      counts[tab.id] !== undefined
        ? html`<span class="tab__count">${counts[tab.id]}</span>` : ''}
    </button>`)}</div>`;
}
