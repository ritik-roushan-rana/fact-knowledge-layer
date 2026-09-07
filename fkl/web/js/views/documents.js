/**
 * Upload and corpus overview.
 *
 * Ingest runs server-side as a background job, so this view polls while any
 * job is active and reports the stage it is in rather than an opaque spinner.
 */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { empty, pill } from '../components/primitives.js';

const ACTIVE = ['queued', 'running'];

function jobLine(job) {
  const d = job.detail || {};
  switch (job.stage) {
    case 'extracting': return d.total ? `Extracting — page ${d.done} of ${d.total}` : 'Extracting';
    case 'grounding': return `Verifying ${d.total ?? ''} pieces of evidence`;
    case 'matching': return `Matching — ${d.candidate_pairs ?? '…'} candidate pairs`;
    case 'adjudicating': return `Adjudicating ${d.done ?? 0}/${d.total ?? ''}`;
    case 'complete': return 'Complete';
    default: return job.stage || job.status;
  }
}

function jobProgress(job) {
  const d = job.detail || {};
  if (job.stage === 'extracting' && d.total) return Math.round((100 * d.done) / d.total);
  return job.status === 'done' ? 100 : null;
}

function jobCard(job) {
  const progress = jobProgress(job);
  const report = job.report;
  return html`
    <div class="job">
      <div class="job__head">
        <span class="job__status job__status--${job.status}">${job.status}</span>
        <span class="claim-panel__file">${job.filename}</span>
        <span class="muted push">${jobLine(job)}${job.elapsed ? ` · ${job.elapsed}s` : ''}</span>
      </div>
      ${progress !== null
        ? html`<div class="progress"><i class="progress__fill"
                 style="width:${progress}%"></i></div>` : ''}
      ${job.error ? html`<div class="cell-note" style="color:var(--bad)">${job.error}</div>` : ''}
      ${report ? html`<div class="cell-note">
          ${report.claims_stored} claims · ${report.claims_grounded} grounded ·
          ${report.claims_quarantined} quarantined${
          report.skipped_resume ? ' · already ingested, skipped' : ''}
        </div>` : ''}
    </div>`;
}

function documentsTable(documents) {
  if (!documents.length) {
    return empty('No documents yet',
      'Drop a PDF above. Extraction runs locally and takes a few seconds per document.', '◍');
  }
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
                ? html`<div class="cell-note" style="color:var(--amber)">
                    ${s.errors.length} warning(s)</div>` : ''}</td>
              <td><button class="btn btn--ghost" data-delete="${doc.doc_id}">Remove</button></td>
            </tr>`;
        })}</tbody>
      </table>
    </div></div>`;
}

export async function render() {
  const [documents, jobs] = await Promise.all([api.documents(), api.jobs()]);
  return html`
    <div class="card"><div class="card__body">
      <div class="dropzone" id="dropzone" role="button" tabindex="0"
           aria-label="Upload a PDF">
        <div class="dropzone__icon" aria-hidden="true">↑</div>
        <div class="dropzone__title">Drop a PDF here, or click to choose</div>
        <div class="dropzone__hint">Any PDF. Deterministic extraction — no API key required.</div>
        <input type="file" id="file-input" accept="application/pdf" multiple hidden>
      </div>
      ${jobs.length ? html`<div class="jobs">${jobs.slice(0, 6).map(jobCard)}</div>` : ''}
    </div></div>
    ${documentsTable(documents)}`;
}

/** Direct bindings the delegated router cannot express (drag events, file input). */
export function mount(root, { refresh }) {
  const dropzone = root.querySelector('#dropzone');
  const input = root.querySelector('#file-input');
  if (!dropzone || !input) return;

  const upload = async (files) => {
    for (const file of files) await api.uploadDocument(file);
    refresh();
  };

  dropzone.addEventListener('click', () => input.click());
  dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault(); dropzone.classList.add('is-active');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-active'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault(); dropzone.classList.remove('is-active');
    upload(e.dataTransfer.files);
  });
  input.addEventListener('change', (e) => upload(e.target.files));
}

/** Whether this view should keep polling. */
export async function isBusy() {
  const jobs = await api.jobs().catch(() => []);
  return jobs.some((job) => ACTIVE.includes(job.status));
}
