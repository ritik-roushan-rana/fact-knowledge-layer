/** One row per ingestion job: filename, what the pipeline is doing right now,
 *  how far along it is, and how long it has been running.
 *
 *  Progress is a band per stage. Where the backend reports counts (pages
 *  extracted, claims grounded, pairs compared) the bar fills inside its band
 *  for real; where it cannot, the bar holds at the band's start and carries a
 *  moving stripe instead — working, but no invented percentage.
 */

import { useEffect, useState } from 'react';
import { html } from '../lib/html.js';

const STATUS_META = {
  queued:  { icon: 'schedule',      label: 'Queued',  chip: 'bg-surface-container-high text-on-surface-variant' },
  running: { icon: 'hourglass_top', label: 'Reading', chip: 'bg-primary-container/15 text-primary-container' },
  done:    { icon: 'check_circle',  label: 'Done',    chip: 'bg-v-corr/10 text-v-corr' },
  failed:  { icon: 'error',         label: 'Failed',  chip: 'bg-v-contra/10 text-v-contra' },
};

/** Where each stage sits on the overall bar, start → end. */
const BANDS = {
  queued:               [0, 2],
  loading:              [2, 8],
  extracting:           [8, 52],
  grounding:            [52, 68],
  stored:               [68, 70],
  relating:             [70, 73],
  matching:             [73, 78],
  comparing:            [78, 92],
  adjudicating:         [92, 97],
  components_confirmed: [97, 98],
  related:              [98, 99],
  complete:             [100, 100],
  skipped:              [100, 100],
};

function stageLabel(job) {
  const d = job.detail || {};
  switch (job.stage) {
    case 'loading':      return 'Reading the document';
    case 'extracting':   return d.total ? `Extracting from page ${d.done} of ${d.total}`
                                        : 'Extracting claims';
    case 'grounding':    return d.total ? `Checking evidence ${d.done ?? 0} of ${d.total}`
                                        : 'Checking evidence';
    case 'stored':       return 'Claims stored';
    case 'relating':     return 'Preparing to compare';
    case 'matching':     return 'Finding claims worth comparing';
    case 'comparing':    return d.total ? `Comparing pair ${d.done ?? 0} of ${d.total}`
                                        : 'Comparing against the layer';
    case 'adjudicating': return `Resolving ambiguous pairs ${d.done ?? 0} of ${d.total ?? ''}`;
    case 'components_confirmed': return 'Confirming component totals';
    case 'related':      return 'Storing relationships';
    case 'complete':     return 'Done';
    case 'skipped':      return 'Already ingested';
    case 'error':        return 'Failed';
    default:             return job.stage || job.status;
  }
}

/** → { pct, measured } — `measured` is false when the stage reports no counts. */
function progressFor(job) {
  if (job.status === 'done')   return { pct: 100, measured: true };
  if (job.status === 'failed') return { pct: null, measured: false };
  const band = BANDS[job.stage] || BANDS[job.status] || [0, 2];
  const d = job.detail || {};
  const ratio = d.total ? Math.min(1, (d.done || 0) / d.total) : null;
  if (ratio === null) return { pct: band[0], measured: false };
  return { pct: Math.round(band[0] + (band[1] - band[0]) * ratio), measured: true };
}

/** The server stamps `elapsed` only when a stage reports in, so a long quiet
 *  stage would freeze the clock. Count from `started_at` in the browser. */
function useElapsed(job) {
  const live = job.status === 'running' || job.status === 'queued';
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!live) return undefined;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [live]);
  if (!live) return job.elapsed ?? null;
  if (!job.started_at) return job.elapsed ?? null;
  return Math.max(0, Math.round(now - job.started_at));
}

export const JobCard = ({ job }) => {
  const meta = STATUS_META[job.status] || STATUS_META.queued;
  const { pct, measured } = progressFor(job);
  const isActive = job.status === 'running' || job.status === 'queued';
  const elapsed = useElapsed(job);

  return html`
    <div className="p-space-md bg-surface-container border border-line rounded-lg
                     flex flex-col gap-space-sm">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-space-xs">
        <div className="flex items-center gap-space-sm min-w-0">
          <span className=${`material-symbols-outlined text-primary-container text-[20px] shrink-0
                              ${job.status === 'running' ? 'animate-pulse' : ''}`}>
            ${meta.icon}
          </span>
          <span className="font-headline-md text-headline-md text-on-surface truncate">
            ${job.filename}
          </span>
        </div>
        <div className="flex items-center gap-space-sm shrink-0">
          <span className=${`font-label-sm text-label-sm px-space-sm py-0.5 rounded-full
                              font-medium flex items-center gap-1 ${meta.chip}`}>
            ${isActive && html`
              <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse"></span>`}
            ${meta.label}
          </span>
          ${pct !== null && measured && html`
            <span className="font-mono text-code-sm text-on-surface-variant tabular">${pct}%</span>`}
        </div>
      </div>

      ${pct !== null && html`
        <div className="w-full bg-surface-container-lowest h-1.5 rounded-full overflow-hidden">
          <div className=${`h-full rounded-full bg-primary-container transition-[width] duration-500
                             ${measured ? '' : 'bar-working'}`}
               style=${{ width: `${Math.max(pct, 2)}%` }}></div>
        </div>`}

      <div className="flex items-center justify-between gap-space-sm flex-wrap
                        font-label-sm text-label-sm">
        <span className="text-on-surface-variant">${stageLabel(job)}</span>
        ${elapsed !== null && html`
          <span className="font-mono text-code-sm text-outline tabular">${elapsed}s</span>`}
      </div>

      ${job.error && html`
        <div className="font-body-sm text-body-sm text-v-contra bg-v-contra/10
                         border border-v-contra/40 rounded-md px-space-md py-space-sm">
          ${job.error}
        </div>`}

      ${job.report && html`
        <div className="flex items-center gap-space-md font-mono text-code-sm text-outline flex-wrap">
          <span className="text-on-surface">${job.report.claims_stored} claims</span>
          <span className="text-v-corr">${job.report.claims_grounded} grounded</span>
          ${job.report.claims_quarantined ? html`
            <span className="text-v-under">${job.report.claims_quarantined} quarantined</span>` : null}
          ${job.report.skipped_resume && html`<span>already ingested, skipped</span>`}
        </div>`}
    </div>`;
};
