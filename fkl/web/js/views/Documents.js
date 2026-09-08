/** Documents — the overview.
 *
 *   Page head · corpus summary row
 *   Compact dropzone + any running ingestion jobs
 *   The document list: one row per PDF, with its verdict counts on the right
 */

import { useEffect, useRef, useState } from 'react';
import { html } from '../lib/html.js';
import { api } from '../lib/api.js';
import { commaNum, ingestedDate, ingestedMs } from '../lib/format.js';
import { useDocuments, useJobs, useStats } from '../lib/hooks.js';
import { CompactDropzone } from '../components/Dropzone.js';
import { JobCard } from '../components/JobCard.js';
import { PageHead, StatRow } from '../components/PageHead.js';

/* ─────────────────────── One document row ─────────────────────── */

const TONE_CHIP = {
  contra:  'text-v-contra bg-v-contra/10',
  recon:   'text-v-recon bg-v-recon/10',
  corr:    'text-v-corr bg-v-corr/10',
  super:   'text-v-super bg-v-super/10',
  under:   'text-v-under bg-v-under/10',
};

const CountChip = ({ label, count, tone }) => html`
  <span className=${`h-6 px-space-sm rounded-full flex items-center gap-1.5 font-label-sm
                      text-label-sm whitespace-nowrap ${TONE_CHIP[tone] || 'text-outline bg-surface-container-high'}`}>
    <span className="font-mono font-semibold tabular">${commaNum(count)}</span>
    <span>${label}</span>
  </span>`;

const DocumentRow = ({ doc, onRemove }) => {
  const s = doc.stats || {};
  const relations = s.relations_by_kind || {};
  const entity = s.extractor_primary_entity;
  const pages = doc.pages_read || doc.n_pages || 0;
  const date = ingestedDate(doc.ingested_at);

  const chips = [
    ['contradicts',  relations.contradiction, 'contra'],
    ['reconciled',   relations.reconciled,    'recon'],
    ['supersedes',   relations.supersedes,    'super'],
    ['corroborates', relations.corroboration, 'corr'],
    ['to review',    s.claims_needing_review, 'under'],
  ].filter(([, c]) => c > 0);

  const meta = [
    `${pages} ${pages === 1 ? 'page' : 'pages'}`,
    `${commaNum(doc.claim_count ?? 0)} facts`,
    date,
    entity && `subject: ${entity}`,
  ].filter(Boolean);

  return html`
    <div className="group flex flex-col lg:flex-row lg:items-center gap-space-md px-space-lg
                     py-space-md border-b border-line last:border-b-0
                     hover:bg-surface-container-low transition-colors">
      <div className="flex items-start gap-space-md min-w-0 flex-1">
        <span className="material-symbols-outlined text-outline text-[20px] mt-0.5
                          group-hover:text-primary-container transition-colors">
          picture_as_pdf
        </span>
        <div className="flex flex-col min-w-0 gap-0.5">
          <span className="font-label-lg text-label-lg text-on-surface truncate">
            ${doc.filename}
          </span>
          <span className="font-body-sm text-body-sm text-outline truncate">
            ${meta.join(' · ')}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-space-xs shrink-0 pl-[36px] lg:pl-0">
        ${chips.length > 0
          ? chips.map(([label, count, tone]) => html`
              <${CountChip} key=${label} label=${label} count=${count} tone=${tone} />`)
          : html`
            <span className="font-label-sm text-label-sm text-outline">No relationships yet</span>`}
        <button type="button" onClick=${() => onRemove(doc.doc_id)} title="Remove document"
                className="w-8 h-8 flex items-center justify-center rounded-md text-outline
                            opacity-0 group-hover:opacity-100 focus:opacity-100
                            hover:text-v-contra hover:bg-v-contra/10 transition-all">
          <span className="material-symbols-outlined text-[18px]">delete</span>
        </button>
      </div>
    </div>`;
};

/** Shown between picking a file and the server answering with a job id. */
const UploadingCard = ({ filename }) => html`
  <div className="p-space-md bg-surface-container border border-line rounded-lg
                   flex items-center gap-space-sm">
    <span className="w-4 h-4 rounded-full border-2 border-primary-container border-t-transparent
                      animate-spin shrink-0"></span>
    <span className="font-headline-md text-headline-md text-on-surface truncate">${filename}</span>
    <span className="ml-auto font-label-sm text-label-sm text-outline shrink-0">Uploading…</span>
  </div>`;

/* ─────────────────────────── The view ─────────────────────────── */

export const DocumentsView = ({ onUploaded }) => {
  const stats = useStats().data || {};
  const { data: documents = [], refresh: refreshDocs } = useDocuments();
  // Poll fast while something is in flight, then back off — an idle workspace
  // does not need a request every 1.5 seconds.
  const [busy, setBusy] = useState(true);
  const activeJobs = useJobs(busy).data || [];
  const running = activeJobs.filter((j) => j.status === 'running' || j.status === 'queued');
  const [sort, setSort] = useState('date');

  // The upload POST only queues a job; the document itself lands later. Watch
  // the job list and refresh once a job actually changes state, otherwise the
  // finished document never appears without a reload.
  const [pending, setPending] = useState([]);
  useEffect(() => { setBusy(running.length > 0 || pending.length > 0); },
            [running.length, pending.length]);
  const statuses = activeJobs.map((j) => `${j.job_id}:${j.status}`).join('|');
  const seen = useRef(statuses);
  useEffect(() => {
    if (seen.current !== statuses) {
      seen.current = statuses;
      refreshDocs();
      onUploaded?.();
    }
  }, [statuses, refreshDocs, onUploaded]);

  const handleUploaded = () => { setPending([]); refreshDocs(); onUploaded?.(); };
  const handleRemove = async (docId) => {
    try { await api.deleteDocument(docId); refreshDocs(); }
    catch { /* ignore */ }
  };

  const sorted = [...documents].sort((a, b) => {
    if (sort === 'facts') return (b.claim_count || 0) - (a.claim_count || 0);
    if (sort === 'name')  return (a.filename || '').localeCompare(b.filename || '');
    return ingestedMs(b.ingested_at) - ingestedMs(a.ingested_at);
  });

  const k = stats.relations_by_kind || {};
  const relationsTotal = Object.entries(k)
    .filter(([kind]) => kind !== 'unrelated')
    .reduce((n, [, c]) => n + c, 0);

  return html`
    <div className="w-full max-w-5xl mx-auto px-space-lg py-space-xl flex flex-col gap-space-lg">
      <${PageHead}
        title="Documents"
        subtitle=${`${documents.length} ${documents.length === 1 ? 'document' : 'documents'} · `
          + `${commaNum(stats.claims ?? 0)} grounded facts · `
          + `${commaNum(relationsTotal)} relationships in one shared layer`} />

      <${StatRow} items=${[
        ['Facts',          stats.claims ?? 0],
        ['From tables',    stats.claims_from_tables ?? 0],
        ['Corroborations', k.corroboration ?? 0, 'corr'],
        ['Contradictions', k.contradiction ?? 0, 'contra'],
        ['Reconciled',     k.reconciled ?? 0, 'recon'],
        ['Quarantined',    stats.claims_quarantined ?? 0, 'under'],
      ]} />

      <${CompactDropzone} onDone=${handleUploaded} onStart=${setPending} />

      ${(running.length > 0 || pending.length > 0) && html`
        <section className="flex flex-col gap-space-sm">
          <h2 className="font-label-lg text-label-lg text-on-surface-variant">
            Processing ${running.length + pending.length}
            ${running.length + pending.length === 1 ? 'file' : 'files'}
          </h2>
          ${pending.map((name) => html`<${UploadingCard} key=${name} filename=${name} />`)}
          ${running.map((j) => html`<${JobCard} key=${j.job_id} job=${j} />`)}
        </section>`}

      ${documents.length > 0 && html`
        <section className="flex flex-col gap-space-sm">
          <div className="flex items-center justify-between gap-space-sm">
            <h2 className="font-label-lg text-label-lg text-on-surface-variant">In the layer</h2>
            <label className="flex items-center gap-space-xs font-label-sm text-label-sm text-outline">
              Sort
              <select value=${sort} onChange=${(e) => setSort(e.target.value)}
                      className="h-7 px-space-xs bg-surface-container border border-line
                                  text-on-surface font-label-sm text-label-sm rounded-md
                                  cursor-pointer focus:outline-none focus:border-primary-container">
                <option value="date">Newest first</option>
                <option value="facts">Most facts</option>
                <option value="name">Name</option>
              </select>
            </label>
          </div>
          <div className="rounded-lg border border-line bg-surface-container-low overflow-hidden">
            ${sorted.map((d) => html`
              <${DocumentRow} key=${d.doc_id} doc=${d} onRemove=${handleRemove} />`)}
          </div>
        </section>`}
    </div>`;
};
