/** Every extracted claim, as a scannable table. */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { evidenceText, pageOf, truncate } from '../core/format.js';
import { confidenceBar, empty, pill } from '../components/primitives.js';

export function searchBar(state, documents, count, noun = 'claim') {
  return html`
    <div class="toolbar">
      <div class="toolbar__group">
        <input class="input" type="search" id="claim-search"
               placeholder="Search subject, predicate or value"
               value="${state.filters.search}" aria-label="Search claims">
        <select class="select" id="document-filter" aria-label="Filter by document">
          <option value="">All documents</option>
          ${documents.map((doc) => (state.filters.docId === doc.doc_id
            ? html`<option value="${doc.doc_id}" selected>${doc.filename}</option>`
            : html`<option value="${doc.doc_id}">${doc.filename}</option>`))}
        </select>
      </div>
      <span class="muted num push" style="font-size:var(--t-xs)">
        ${count} ${noun}${count === 1 ? '' : 's'}</span>
    </div>`;
}

function contextCell(claim) {
  const values = [claim.ctx_period, claim.ctx_unit, claim.ctx_scope, claim.ctx_basis]
    .filter(Boolean);
  if (!values.length) return html`<span class="context__none">not stated</span>`;
  return html`<div class="context">${values.map((v) => html`
    <span class="context__item">${v}</span>`)}</div>`;
}

export async function render(state) {
  const [claims, documents] = await Promise.all([
    api.claims({ search: state.filters.search, doc_id: state.filters.docId }),
    api.documents(),
  ]);
  const bar = searchBar(state, documents, claims.length);

  if (!claims.length) {
    const filtering = state.filters.search || state.filters.docId;
    return html`${bar}${empty(
      filtering ? 'No claims match' : 'No claims yet',
      filtering
        ? 'Try a shorter search term, or clear the document filter.'
        : 'Upload a PDF and its claims will appear here.',
      filtering ? '⌕' : '◍',
    )}`;
  }

  return html`${bar}
    <div class="card"><div class="scroll-x">
      <table class="table">
        <thead><tr>
          <th>Subject</th><th>Predicate</th><th class="is-numeric">Value</th>
          <th>Context</th><th>Evidence</th><th class="is-numeric">Confidence</th>
        </tr></thead>
        <tbody>${claims.map((claim) => html`
          <tr>
            <td>${claim.subject}</td>
            <td class="cell-title">${claim.predicate}</td>
            <td class="cell-value is-numeric">${claim.value}</td>
            <td>${contextCell(claim)}</td>
            <td>
              ${pill(`p.${pageOf(claim)}`)} ${pill(claim.origin || 'sentence')}
              <div class="cell-note">${truncate(evidenceText(claim))}</div>
            </td>
            <td class="is-numeric">${confidenceBar(claim.final_confidence)}</td>
          </tr>`)}
        </tbody>
      </table>
    </div></div>`;
}
