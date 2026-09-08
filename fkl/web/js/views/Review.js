/** Needs review — everything the pipeline was not confident about.
 *
 *   Quarantined claims are held out of the shared layer entirely;
 *   soft-flagged ones are admitted but carry a warning. Both are shown with
 *   the reason they were flagged and the text they were read out of.
 */

import { useMemo, useState } from 'react';
import { html } from '../lib/html.js';
import { commaNum } from '../lib/format.js';
import { useClaims } from '../lib/hooks.js';
import { ClaimList } from '../components/ClaimList.js';
import { PageHead } from '../components/PageHead.js';

/* ─────────────── Categorise a claim into a filter bucket ─────────────── */

function categoriseReason(claim) {
  const reasons = (claim.review_reasons || []).join(' ').toLowerCase();
  if (claim.quarantined || reasons.includes('ground') || reasons.includes('anchor')
      || reasons.includes('relocate') || reasons.includes('span')) return 'grounding';
  if (reasons.includes('context') || reasons.includes('period')
      || reasons.includes('unit') || reasons.includes('scope')) return 'context';
  if (reasons.includes('vocab') || reasons.includes('predicate')
      || reasons.includes('entity') || reasons.includes('canonical')) return 'vocab';
  return 'other';
}

const FILTERS = [
  { id: 'all',       label: 'All flagged' },
  { id: 'quar',      label: 'Quarantined' },
  { id: 'grounding', label: 'Low grounding' },
  { id: 'context',   label: 'Missing context' },
  { id: 'vocab',     label: 'Unmatched vocabulary' },
];

const FilterChip = ({ label, count, active, onClick }) => html`
  <button type="button" onClick=${onClick}
          className=${`h-8 px-space-md rounded-full border font-label-md text-label-md
                        flex items-center gap-space-xs whitespace-nowrap transition-colors
                        ${active
                          ? 'bg-surface-container-high border-line-strong text-on-surface'
                          : 'bg-transparent border-line text-on-surface-variant hover:bg-surface-container'}`}>
    <span>${label}</span>
    <span className="font-mono text-code-sm text-outline tabular">${commaNum(count)}</span>
  </button>`;

const SectionHeader = ({ label, hint, count, tone }) => html`
  <div className="flex flex-wrap items-baseline justify-between gap-space-sm pt-space-sm">
    <h2 className="font-headline-md text-headline-md text-on-surface flex items-center gap-space-sm">
      <span className=${`w-2 h-2 rounded-full ${tone}`}></span>
      ${label}
      <span className="font-mono text-code-sm text-outline">${commaNum(count)}</span>
    </h2>
    ${hint && html`<span className="font-label-sm text-label-sm text-outline">${hint}</span>`}
  </div>`;

/* ─────────────── The view ─────────────── */

export const ReviewView = () => {
  const [filter, setFilter] = useState('all');
  const { data: quarantined = [], loading: qLoading }
    = useClaims({ quarantined: true, limit: 200 });
  const { data: reviewList = [], loading: rLoading }
    = useClaims({ needs_review: true, quarantined: false, limit: 200 });

  const buckets = useMemo(() => {
    const b = {
      all: quarantined.length + reviewList.length,
      quar: quarantined.length,
      grounding: 0, context: 0, vocab: 0, other: 0,
    };
    reviewList.forEach((c) => { const k = categoriseReason(c); b[k] = (b[k] || 0) + 1; });
    return b;
  }, [quarantined, reviewList]);

  const matches = (claim) => {
    if (filter === 'all')  return true;
    if (filter === 'quar') return claim.quarantined;
    return !claim.quarantined && categoriseReason(claim) === filter;
  };

  const filteredQuar = quarantined.filter(matches);
  const filteredReview = reviewList.filter(matches);
  const total = filteredQuar.length + filteredReview.length;
  const totalAll = quarantined.length + reviewList.length;

  const body = () => {
    if (qLoading || rLoading) return [1, 2, 3].map((i) => html`
      <div key=${i} className="h-64 rounded-lg bg-surface-container-low animate-pulse"></div>`);

    if (totalAll === 0) return html`
      <div className="rounded-lg border border-line bg-surface-container-low px-space-lg
                        py-space-xl flex flex-col items-center text-center gap-space-sm">
        <span className="material-symbols-outlined text-[24px] text-v-corr">verified</span>
        <div className="font-headline-md text-headline-md text-on-surface">Nothing flagged</div>
        <p className="font-body-sm text-body-sm text-on-surface-variant max-w-sm">
          Every claim in the layer passed grounding and confidence checks.
        </p>
      </div>`;

    if (total === 0) return html`
      <div className="rounded-lg border border-line bg-surface-container-low px-space-lg
                        py-space-lg text-center font-body-md text-body-md text-on-surface-variant">
        No claims match this filter.
      </div>`;

    return html`
      ${filteredQuar.length > 0 && html`
        <section className="flex flex-col gap-space-md">
          <${SectionHeader} label="Quarantined" count=${filteredQuar.length} tone="bg-v-contra"
                             hint="kept out of comparison" />
          <${ClaimList} claims=${filteredQuar} secondary="reason" />
        </section>`}
      ${filteredReview.length > 0 && html`
        <section className="flex flex-col gap-space-md">
          <${SectionHeader} label="Flagged" count=${filteredReview.length} tone="bg-v-under"
                             hint="in the layer, with a warning" />
          <${ClaimList} claims=${filteredReview} secondary="reason" />
        </section>`}`;
  };

  return html`
    <div className="w-full max-w-5xl mx-auto px-space-lg py-space-xl flex flex-col gap-space-lg">
      <${PageHead}
        title="Needs review"
        subtitle=${totalAll === 0
          ? 'Nothing is waiting on you.'
          : `${commaNum(totalAll)} ${totalAll === 1 ? 'claim' : 'claims'} the pipeline `
            + 'was not confident about.'} />

      <div className="flex flex-wrap items-center gap-space-xs">
        ${FILTERS.map((f) => html`
          <${FilterChip} key=${f.id} label=${f.label} count=${buckets[f.id] ?? 0}
                          active=${filter === f.id} onClick=${() => setFilter(f.id)} />`)}
      </div>

      <div className="flex flex-col gap-space-lg">${body()}</div>
    </div>`;
};
