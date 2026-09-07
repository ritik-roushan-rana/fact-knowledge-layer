/**
 * One claim, shown as evidence: where it came from, what it asserts, the
 * context that qualifies it, and the text it was grounded against.
 */

import { html } from '../core/dom.js';
import { evidenceText, pageOf, score, statedContext } from '../core/format.js';
import { pill } from './primitives.js';

export function contextChips(claim) {
  const stated = statedContext(claim);
  if (!stated.length) {
    return html`<div class="context"><span class="context__none">no context stated</span></div>`;
  }
  return html`<div class="context">${stated.map(([label, value]) => html`
    <span class="context__item"><span class="context__key">${label}</span>${value}</span>`)}</div>`;
}

export function claimTriple(claim) {
  return html`
    <div class="claim-panel__triple">
      <span class="claim-panel__subject">${claim.subject}</span>
      <span class="claim-panel__predicate">${claim.predicate}</span>
      <span class="claim-panel__value">${claim.value}</span>
    </div>`;
}

export function claimPanel(claim) {
  if (!claim) {
    return html`<div class="claim-panel"><span class="context__none">claim unavailable</span></div>`;
  }
  return html`
    <div class="claim-panel">
      <div class="claim-panel__source">
        <span class="claim-panel__file">${claim.source_document}</span>
        ${pill(`p.${pageOf(claim)}`)}
        ${pill(claim.origin || 'sentence')}
        ${claim.bbox ? pill('bbox') : ''}
      </div>
      ${claimTriple(claim)}
      ${contextChips(claim)}
      <blockquote class="evidence">${evidenceText(claim)}</blockquote>
      <div class="claim-panel__meta">
        <span>extraction ${score(claim.extraction_confidence)}</span>
        <span>grounding ${score(claim.grounding_score)}</span>
        ${claim.modality && claim.modality !== 'reported'
          ? html`<span>${claim.modality}</span>` : ''}
        ${claim.value_in_span === false
          ? html`<span class="is-bad">value not in evidence</span>` : ''}
      </div>
    </div>`;
}
