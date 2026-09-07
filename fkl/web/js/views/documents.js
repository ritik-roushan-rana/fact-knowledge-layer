/**
 * Upload and corpus overview.
 *
 * Two shapes, chosen by whether anything has been ingested yet: an empty
 * corpus gets the upload panel as the whole page, because adding a document is
 * the only useful action; once documents exist the panel shrinks and the
 * corpus table takes over.
 */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { empty, pill } from '../components/primitives.js';
import { bindUploader, hasActiveJobs, jobList, uploadPanel } from '../components/uploader.js';

function documentsTable(documents) {
  return html`
    <div class="card"><div class="scroll-x">
      <table class="table">
        <thead><tr>
          <th>Document</th><th class="is-numeric">Pages</th><th class="is-numeric">Claims</th>
          <th class="is-numeric">Tables</th><th>Detected entity</th><th>Status</th>
          <th><span class="visually-hidden">Actions</span></th>
        </tr></thead>
        <tbody>${documents.map((doc) => {
          const s = doc.stats || {};
          return html`
            <tr>
              <td class="cell-title">${doc.filename}
                <div class="cell-note mono">${doc.doc_id}</div></td>
              <td class="is-numeric">${doc.pages_read}${doc.pages_read !== doc.n_pages
                ? ` / ${doc.n_pages}` : ''}</td>
              <td class="is-numeric">${doc.claim_count}</td>
              <td class="is-numeric">${s.extractor_tables_found ?? '—'}</td>
              <td>${s.extractor_primary_entity ?? '—'}</td>
              <td>${pill(doc.status)}${(s.errors || []).length
                ? html`<div class="cell-note">${s.errors.length} warning(s)</div>` : ''}</td>
              <td><button class="btn btn--ghost" type="button"
                          data-delete="${doc.doc_id}">Remove</button></td>
            </tr>`;
        })}</tbody>
      </table>
    </div></div>`;
}

export async function render() {
  const [documents, jobs] = await Promise.all([api.documents(), api.jobs()]);

  // Nothing ingested yet: the upload panel is the page.
  if (!documents.length) {
    return html`
      ${uploadPanel(jobs, 'hero')}
      ${jobs.length ? '' : empty(
        'No documents yet',
        'Add a PDF above. Once two documents cover overlapping facts, their claims are compared and the relationships appear under Relationships.',
        '◍',
      )}`;
  }

  return html`
    ${uploadPanel(jobs, 'compact')}
    ${documentsTable(documents)}`;
}

/** Drag-and-drop and file-picker bindings the delegated router cannot express. */
export function mount(root, { refresh }) {
  bindUploader(root, { refresh });
}

/** Whether this view should keep polling. */
export async function isBusy() {
  const jobs = await api.jobs().catch(() => []);
  return hasActiveJobs(jobs);
}

/** Re-exported so app.js can render the job list without importing the view. */
export { jobList };
