/**
 * Claims the grounding step refused to accept.
 *
 * Shown as a comparison rather than a count: what the extractor claimed to be
 * quoting, next to what the document actually contains at the closest match.
 * That contrast is what makes an extraction failure legible.
 */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { pageOf } from '../core/format.js';
import { empty, pill } from '../components/primitives.js';
import { claimTriple, contextChips } from '../components/claim-panel.js';
import { searchBar } from './claims.js';

export async function render(state) {
  const [claims, documents] = await Promise.all([
    api.claims({ needs_review: true, search: state.filters.search, doc_id: state.filters.docId }),
    api.documents(),
  ]);
  const bar = searchBar(state, documents, claims.length);

  if (!claims.length) {
    return html`${bar}${empty(
      'Nothing flagged for review',
      'Every extracted claim was located in its source document.',
      '✓',
    )}`;
  }

  return html`${bar}<div class="relation-list">${claims.map((claim) => html`
    <article class="card relation relation--${claim.quarantined ? 'contradiction' : 'underspecified'}">
      <header class="card__head">
        <span class="badge badge--${claim.quarantined ? 'contradiction' : 'underspecified'}">
          ${claim.quarantined ? 'Quarantined' : 'Needs review'}</span>
        <span class="claim-panel__file">${claim.source_document}</span>
        ${pill(`claimed p.${claim.span_page}`)}
        ${claim.grounding_page && claim.grounding_page !== claim.span_page
          ? pill(`found p.${pageOf(claim)}`) : ''}
      </header>
      <div class="claim-panel">
        ${claimTriple(claim)}
        ${contextChips(claim)}
        <p class="evidence__label">What the extractor quoted</p>
        <blockquote class="evidence">${claim.span_text}</blockquote>
        <p class="evidence__label">Closest text actually in the document</p>
        <blockquote class="evidence evidence--plain">${claim.matched_text
          || '— not found anywhere in the document —'}</blockquote>
        <ul class="review-reasons">${(claim.review_reasons || [])
          .map((reason) => html`<li>${reason}</li>`)}</ul>
      </div>
    </article>`)}</div>`;
}
