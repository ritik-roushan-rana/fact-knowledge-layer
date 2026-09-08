/** A list of claims, built for scanning rather than reading.
 *
 *  One line per claim — status dot, the statement, its source, the value in
 *  a column of its own — and the evidence, scores and source page behind a
 *  click. Used by both Claims and Needs review; the second line of each row
 *  carries the stated context there, and the reason it was flagged here.
 */

import { useState } from 'react';
import { html } from '../lib/html.js';
import {
  CONTEXT_FIELDS, evidenceText, pageOf, score, statedContext,
} from '../lib/format.js';

const STATUS = {
  quarantined:  ['bg-v-contra', 'Quarantined — held out of the shared layer'],
  needs_review: ['bg-v-under',  'Flagged for review'],
  grounded:     ['bg-v-corr',   'Grounded'],
};

const statusOf = (c) => (c.quarantined ? 'quarantined' : c.needs_review ? 'needs_review' : 'grounded');

/** The context a claim states, as one compact line: "Q1 FY23 · INR crore". */
function contextLine(claim) {
  return CONTEXT_FIELDS
    .map(([key, label]) => [label, claim[`ctx_${key}`]])
    .filter(([, v]) => v)
    .map(([label, v]) => (label === 'period' || label === 'unit' ? v : `${label} ${v}`))
    .join(' · ');
}

const shortName = (name = '') => name.replace(/\.pdf$/i, '');

/** The evidence sentence, with the claimed value marked inside it. */
function markedEvidence(claim) {
  const text = evidenceText(claim);
  const value = String(claim.value ?? '').trim();
  const idx = value && text ? text.indexOf(value) : -1;
  if (idx < 0) return html`<span>${text}</span>`;
  return html`
    <span>${text.slice(0, idx)}</span>
    <mark className="bg-primary-container/20 text-primary rounded-sm px-0.5">${value}</mark>
    <span>${text.slice(idx + value.length)}</span>`;
}

/* ─────────────────────── The opened row ─────────────────────── */

const Detail = ({ claim }) => {
  const stated = statedContext(claim);
  const hasPreview = claim.bbox && claim.claim_id;
  const [pdf, setPdf] = useState(false);
  const text = evidenceText(claim);
  const value = String(claim.value ?? '').trim();
  const missing = value && text && !text.includes(value);

  return html`
    <div className="px-space-md pb-space-md pl-[38px] flex flex-col gap-space-md">
      <blockquote className=${`pl-space-md border-l-2 font-body-sm text-body-sm max-w-3xl
                                ${missing ? 'border-v-contra' : 'border-line-strong'}
                                text-on-surface-variant`}>
        ${text
          ? markedEvidence(claim)
          : html`<span className="italic text-outline">No source text located.</span>`}
      </blockquote>
      ${missing && html`
        <span className="font-label-sm text-label-sm text-v-contra">
          “${value}” does not appear in that text.
        </span>`}

      ${stated.length > 0 && html`
        <div className="flex flex-wrap items-center gap-space-xs">
          ${stated.map(([label, v]) => html`
            <span key=${label}
                  className="h-5 px-space-sm rounded-full bg-surface-container-high font-label-sm
                              text-label-sm text-on-surface-variant inline-flex items-center">
              ${label} ${v}
            </span>`)}
        </div>`}

      <div className="flex flex-wrap items-center gap-space-md font-mono text-code-sm text-outline">
        <span>${claim.source_document} · p.${pageOf(claim)}</span>
        <span>extraction ${score(claim.extraction_confidence)}</span>
        <span className=${claim.quarantined ? 'text-v-contra' : ''}>
          grounding ${score(claim.grounding_score)}
        </span>
        ${claim.modality && claim.modality !== 'reported' && html`
          <span className="text-primary-container">${claim.modality}</span>`}
        ${hasPreview && html`
          <button type="button" onClick=${() => setPdf(!pdf)}
                  className="font-sans font-label-sm text-label-sm text-on-surface-variant
                              hover:text-primary-container transition-colors">
            ${pdf ? 'Hide source' : 'View in source PDF'}
          </button>`}
      </div>

      ${pdf && hasPreview && html`
        <div className="p-space-sm rounded-md bg-surface-container-lowest border border-line
                          max-w-2xl">
          <img loading="lazy" src=${`/api/claims/${claim.claim_id}/preview.png?zoom=crop`}
               alt=${`Page ${pageOf(claim)} of ${claim.source_document}`}
               className="max-w-full h-auto rounded-md bg-white" />
          <a href=${`/api/claims/${claim.claim_id}/preview.png?zoom=page`}
             target="_blank" rel="noopener"
             className="mt-space-xs inline-block font-label-sm text-label-sm text-outline
                         hover:text-primary-container">Open the whole page</a>
        </div>`}
    </div>`;
};

/* ─────────────────────────── The row ─────────────────────────── */

const ClaimRow = ({ claim, secondary, open, onToggle }) => {
  const [dot, title] = STATUS[statusOf(claim)];
  const reasons = (claim.review_reasons || []).join(' · ');
  const showReason = secondary === 'reason';
  const line = showReason
    ? (reasons || `grounding ${score(claim.grounding_score)}, below the threshold`)
    : contextLine(claim);

  return html`
    <div className=${open ? 'bg-surface-container-low' : ''}>
      <button type="button" onClick=${onToggle} aria-expanded=${open}
              className="w-full flex items-center gap-space-md px-space-md py-space-sm text-left
                          hover:bg-surface-container-low transition-colors">
        <span className=${`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} title=${title}></span>

        <span className="flex-1 min-w-0 flex flex-col">
          <span className="font-body-md text-body-md text-on-surface truncate">
            ${claim.subject}
            <span className="text-outline"> — </span>
            <span className="text-on-surface-variant">${claim.predicate}</span>
          </span>
          ${line && html`
            <span className="font-label-sm text-label-sm text-outline truncate">${line}</span>`}
        </span>

        <span className="hidden lg:flex w-[190px] shrink-0 items-baseline gap-space-xs
                          font-label-sm text-label-sm text-outline"
              title=${claim.source_document}>
          <span className="font-mono shrink-0">p.${pageOf(claim)}</span>
          <span className="truncate">${shortName(claim.source_document)}</span>
        </span>

        <span className=${`w-[110px] shrink-0 text-right font-mono text-data-md tabular truncate
                            ${claim.quarantined ? 'text-v-contra' : 'text-on-surface'}`}>
          ${claim.value}
        </span>

        <span className=${`material-symbols-outlined text-[18px] text-outline shrink-0
                            transition-transform ${open ? 'rotate-90' : ''}`}>
          chevron_right
        </span>
      </button>
      ${open && html`<${Detail} claim=${claim} />`}
    </div>`;
};

/* ─────────────────────────── The list ─────────────────────────── */

export const ClaimList = ({ claims = [], secondary = 'context' }) => {
  const [openId, setOpenId] = useState(null);
  if (!claims.length) return null;
  return html`
    <div className="rounded-lg border border-line bg-surface-container-lowest/40 overflow-hidden">
      <div className="flex items-center gap-space-md px-space-md py-space-xs border-b border-line
                        font-label-sm text-label-sm text-outline">
        <span className="w-1.5 shrink-0"></span>
        <span className="flex-1">Claim</span>
        <span className="hidden lg:block w-[190px] shrink-0">Source</span>
        <span className="w-[110px] shrink-0 text-right">Value</span>
        <span className="w-[18px] shrink-0"></span>
      </div>
      <div className="divide-y divide-line">
        ${claims.map((c) => html`
          <${ClaimRow} key=${c.claim_id} claim=${c} secondary=${secondary}
                        open=${openId === c.claim_id}
                        onToggle=${() => setOpenId(openId === c.claim_id ? null : c.claim_id)} />`)}
      </div>
    </div>`;
};
