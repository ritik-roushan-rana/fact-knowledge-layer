/** Every extracted claim, as a scannable table. */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { evidenceText, pageOf, truncate } from '../core/format.js';
import { confidenceBar, empty, pill } from '../components/primitives.js';

export function searchBar(state, documents, count, noun = 'claim') {
  return html`
    <div class="toolbar">
      <input class="input" type="search" id="claim-search"
             placeholder="Search subject, predicate or value"
             value="${state.filters.search}" aria-label="Search claims">
      <select class="select" id="document-filter" aria-label="Filter by document">
        <option value="">All documents</option>
        ${documents.map((doc) => (state.filters.docId === doc.doc_id
          ? html`<option value="${doc.doc_id}" selected>${doc.filename}</option>`
          : html`<option value="${doc.doc_id}">${doc.filename}</option>`))}
      </select>
      <span class="muted num push">${count} ${noun}${count === 1 ? '' : 's'}</span>
    </div>`;
}

function contextCell(claim) {
  const values = [claim.ctx_period, claim.ctx_unit, claim.ctx_scope, claim.ctx_basis]
    .filter(Boolean);
  if (!values.length) return html`<span class="context__none">—</span>`;
  return html`${values.map(pill)}`;
}

export async function render(state) {
  const [claims, documents] = await Promise.all([
    api.claims({ search: state.filters.search, doc_id: state.filters.docId }),
    api.documents(),
  ]);
  const bar = searchBar(state, documents, claims.length);
  if (!claims.length) return html`${bar}${empty('No claims yet', 'Upload a PDF to begin.')}`;

  return html`${bar}
    <div class="card"><div class="scroll-x">
      <table class="table">
        <thead><tr>
          <th>Subject</th><th>Predicate</th><th>Value</th>
          <th>Context</th><th>Evidence</th><th>Confidence</th>
        </tr></thead>
        <tbody>${claims.map((claim) => html`
          <tr>
            <td>${claim.subject}</td>
            <td><b>${claim.predicate}</b></td>
            <td class="cell-value">${claim.value}</td>
            <td>${contextCell(claim)}</td>
            <td>
              ${pill(`p.${pageOf(claim)}`)} ${pill(claim.origin || 'sentence')}
              <div class="cell-note">${truncate(evidenceText(claim))}</div>
            </td>
            <td>${confidenceBar(claim.final_confidence)}</td>
          </tr>`)}
        </tbody>
      </table>
    </div></div>`;
}
