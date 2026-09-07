/**
 * PDF upload: the dropzone, the window-wide drop target, and the job list.
 *
 * Shared rather than living in one view, because upload is the first thing a
 * new user does and needs to be reachable from anywhere — the landing state,
 * the Documents tab, and by dropping a file on any part of the window.
 *
 * Ingest runs server-side as a background job, so the UI's job here is to
 * report which stage a document is in, not to block on it.
 */

import { api } from '../core/api.js';
import { html, qs } from '../core/dom.js';

const ACTIVE = ['queued', 'running'];
const isPdf = (file) => file.type === 'application/pdf'
  || file.name.toLowerCase().endsWith('.pdf');

/* ------------------------------------------------------------- markup */

export const dropzone = (variant = 'default') => html`
  <div class="dropzone ${variant === 'hero' ? 'dropzone--hero' : ''} ${
       variant === 'compact' ? 'dropzone--compact' : ''}"
       id="dropzone" role="button" tabindex="0"
       aria-label="Upload a PDF">
    <div class="dropzone__icon" aria-hidden="true">↑</div>
    <div>
      <div class="dropzone__title">Drop a PDF here, or click to choose</div>
      <div class="dropzone__hint">${variant === 'hero'
        ? 'Claims are extracted, each one checked against the text it came from, then compared across every document you add.'
        : 'You can also drop a file anywhere on this page.'}</div>
    </div>
    ${variant === 'hero' ? html`
      <div class="dropzone__meta">
        <span>Runs locally</span><span>No API key needed</span>
        <span>About a second per page</span>
      </div>` : ''}
    <input type="file" id="file-input" accept="application/pdf,.pdf" multiple hidden>
  </div>`;

/** Full-window drop target, revealed while a file is being dragged over. */
export const dropOverlay = () => html`
  <div class="drop-overlay" id="drop-overlay" hidden>
    <div class="drop-overlay__panel">
      <div class="dropzone__icon" aria-hidden="true">↑</div>
      <div class="drop-overlay__title">Drop to add to the knowledge layer</div>
      <div class="drop-overlay__hint">PDF files only</div>
    </div>
  </div>`;

function stageLabel(job) {
  const d = job.detail || {};
  switch (job.stage) {
    case 'loading': return 'Reading the document';
    case 'extracting': return d.total ? `Extracting claims — page ${d.done} of ${d.total}`
                                      : 'Extracting claims';
    case 'grounding': return `Checking ${d.total ?? ''} pieces of evidence`;
    case 'matching': return `Comparing against the corpus — ${d.candidate_pairs ?? '…'} pairs`;
    case 'adjudicating': return `Resolving ambiguous pairs ${d.done ?? 0}/${d.total ?? ''}`;
    case 'complete': return 'Done';
    case 'skipped': return 'Already ingested';
    default: return job.stage || job.status;
  }
}

function progressFor(job) {
  const d = job.detail || {};
  if (job.status === 'done') return 100;
  if (job.stage === 'extracting' && d.total) return Math.round((100 * d.done) / d.total);
  return null; // known to be working, but not measurable yet
}

export function jobCard(job) {
  const pct = progressFor(job);
  const active = ACTIVE.includes(job.status);
  const report = job.report;
  return html`
    <div class="job job--${job.status}">
      <div class="job__head">
        <span class="job__status">${job.status}</span>
        <span class="job__name mono">${job.filename}</span>
        <span class="job__stage push">${stageLabel(job)}${
          job.elapsed ? ` · ${job.elapsed}s` : ''}</span>
      </div>
      ${active || pct !== null ? html`
        <div class="progress ${pct === null ? 'progress--indeterminate' : ''}">
          <i class="progress__fill" style="${pct === null ? '' : `width:${pct}%`}"></i>
        </div>` : ''}
      ${job.error ? html`<div class="job__error">${job.error}</div>` : ''}
      ${report ? html`<div class="job__result">
          ${report.claims_stored} claims · ${report.claims_grounded} grounded ·
          ${report.claims_quarantined} quarantined${
          report.skipped_resume ? ' · already ingested, skipped' : ''}
        </div>` : ''}
    </div>`;
}

export const jobList = (jobs) => (jobs.length
  ? html`<div class="jobs">${jobs.slice(0, 6).map(jobCard)}</div>`
  : '');

/** Dropzone + any in-flight jobs, as one block. */
export const uploadPanel = (jobs = [], variant = 'default') => html`
  <div class="uploader">
    ${dropzone(variant)}
    <div id="upload-error"></div>
    ${jobList(jobs)}
  </div>`;

/* ------------------------------------------------------------ behaviour */

async function send(files, { refresh }) {
  const list = [...files];
  const rejected = list.filter((f) => !isPdf(f));
  const accepted = list.filter(isPdf);

  const slot = qs('#upload-error');
  if (slot) {
    slot.innerHTML = rejected.length
      ? `<div class="upload-error">${rejected.length === 1
          ? `“${rejected[0].name}” is not a PDF. Only PDF files can be added.`
          : `${rejected.length} files were skipped because they are not PDFs.`}</div>`
      : '';
  }
  if (!accepted.length) return;

  for (const file of accepted) {
    try {
      await api.uploadDocument(file);
    } catch (error) {
      if (slot) slot.innerHTML = `<div class="upload-error">Could not upload “${file.name}”: ${error.message}</div>`;
    }
  }
  refresh();
}

/** Wire the in-page dropzone. Call after the view renders. */
export function bindUploader(root, { refresh }) {
  const zone = root.querySelector('#dropzone');
  const input = root.querySelector('#file-input');
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); input.click(); }
  });
  zone.addEventListener('dragover', (event) => {
    event.preventDefault(); zone.classList.add('is-active');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('is-active'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault(); zone.classList.remove('is-active');
    send(event.dataTransfer.files, { refresh });
  });
  input.addEventListener('change', (event) => {
    send(event.target.files, { refresh });
    event.target.value = '';   // let the same file be chosen again
  });
}

/**
 * Window-wide drop. Bound once at startup so a file can be dropped on any
 * view without first navigating to the upload panel.
 *
 * dragenter/dragleave fire for every child element, so nesting is counted
 * rather than toggled — otherwise the overlay flickers as the pointer crosses
 * elements underneath it.
 */
export function bindGlobalDrop({ refresh }) {
  const overlay = qs('#drop-overlay');
  if (!overlay) return;
  let depth = 0;

  const carriesFiles = (event) =>
    [...(event.dataTransfer?.types || [])].includes('Files');

  window.addEventListener('dragenter', (event) => {
    if (!carriesFiles(event)) return;
    depth += 1;
    overlay.hidden = false;
  });
  window.addEventListener('dragover', (event) => {
    if (carriesFiles(event)) event.preventDefault();
  });
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) overlay.hidden = true;
  });
  window.addEventListener('drop', (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    depth = 0; overlay.hidden = true;
    send(event.dataTransfer.files, { refresh });
  });
}

/** Open the file picker from anywhere (the topbar button). */
export function openFilePicker() {
  const input = qs('#file-input');
  if (input) input.click();
}

export const hasActiveJobs = (jobs) => jobs.some((job) => ACTIVE.includes(job.status));
