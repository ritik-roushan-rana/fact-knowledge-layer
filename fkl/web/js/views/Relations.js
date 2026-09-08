/** Relationships — every verdict the layer has drawn between two claims.
 *
 *   Page head · filter row (one chip per verdict kind that exists)
 *   A vertical stack of RelationCards
 */

import { useState } from 'react';
import { html } from '../lib/html.js';
import { api } from '../lib/api.js';
import { commaNum, toneStyle, VERDICTS, verdictLabel } from '../lib/format.js';
import { useRelations, useStats } from '../lib/hooks.js';
import { RelationCard } from '../components/RelationCard.js';
import { PageHead } from '../components/PageHead.js';

/* ───────────────────────── Filter chips ───────────────────────── */

const Chip = ({ label, count, active, tone, onClick }) => html`
  <button type="button" onClick=${onClick}
          className=${`h-8 px-space-md rounded-full font-label-md text-label-md flex items-center
                        gap-space-xs transition-colors whitespace-nowrap border
                        ${active
                          ? 'bg-surface-container-high border-line-strong text-on-surface'
                          : 'bg-transparent border-line text-on-surface-variant hover:bg-surface-container'}`}>
    ${tone && html`<span className=${`w-1.5 h-1.5 rounded-full ${tone.dot}`}></span>`}
    <span>${label}</span>
    ${count !== undefined && html`
      <span className="font-mono text-code-sm text-outline tabular">${commaNum(count)}</span>`}
  </button>`;

const FilterRow = ({ activeKind, counts, total, onKind, onRebuild }) => {
  const kinds = VERDICTS.filter((k) => counts[k]);
  return html`
    <div className="flex flex-wrap items-center gap-space-xs">
      <${Chip} label="All" count=${total} active=${activeKind === ''} onClick=${() => onKind('')} />
      ${kinds.map((k) => html`
        <${Chip} key=${k} label=${verdictLabel(k)} count=${counts[k]} tone=${toneStyle(k)}
                  active=${activeKind === k} onClick=${() => onKind(k)} />`)}
      <button type="button" onClick=${onRebuild}
              className="ml-auto h-8 px-space-md rounded-full border border-line
                          font-label-md text-label-md text-on-surface-variant
                          hover:text-on-surface hover:bg-surface-container transition-colors
                          flex items-center gap-1.5">
        <span className="material-symbols-outlined text-[16px]">refresh</span>
        Rebuild
      </button>
    </div>`;
};

/* ─────────────────────────── View ─────────────────────────── */

const EmptyMessage = ({ hasFilter }) => html`
  <div className="rounded-lg border border-line bg-surface-container-low px-space-lg py-space-xl
                   flex flex-col items-center text-center gap-space-sm">
    <span className="material-symbols-outlined text-[24px] text-outline">
      ${hasFilter ? 'search_off' : 'compare_arrows'}
    </span>
    <div className="font-headline-md text-headline-md text-on-surface">
      ${hasFilter ? 'No relationships of this kind' : 'Nothing to compare yet'}
    </div>
    <p className="font-body-sm text-body-sm text-on-surface-variant max-w-sm">
      ${hasFilter
        ? 'Clear the filter to see the other verdicts.'
        : 'Add a second PDF that covers overlapping facts, and comparisons appear here.'}
    </p>
  </div>`;

export const RelationsView = () => {
  const [kind, setKind] = useState('');
  const stats = useStats().data || {};
  const { data: relations = [], loading, refresh } = useRelations({ kind });
  const counts = stats.relations_by_kind || {};

  const total = Object.entries(counts)
    .filter(([k]) => k !== 'unrelated')
    .reduce((n, [, c]) => n + c, 0);

  const handleRebuild = async () => {
    try {
      await api.rebuildRelations();
      setTimeout(refresh, 3000);
    } catch { /* ignore */ }
  };

  return html`
    <div className="w-full max-w-5xl mx-auto px-space-lg py-space-xl flex flex-col gap-space-lg">
      <${PageHead}
        title="Relationships"
        subtitle=${`${commaNum(total)} ${total === 1 ? 'verdict' : 'verdicts'} drawn across `
          + `${stats.documents ?? 0} ${stats.documents === 1 ? 'document' : 'documents'}`} />

      <${FilterRow} activeKind=${kind} counts=${counts} total=${total}
                     onKind=${setKind} onRebuild=${handleRebuild} />

      <div className="flex flex-col gap-space-lg">
        ${loading && [1, 2, 3].map((i) => html`
          <div key=${i} className="h-56 rounded-lg bg-surface-container-low animate-pulse"></div>`)}
        ${!loading && relations.length === 0 && html`<${EmptyMessage} hasFilter=${!!kind} />`}
        ${!loading && relations.map((r) => html`
          <${RelationCard} key=${r.relation_id} relation=${r} />`)}
      </div>
    </div>`;
};
