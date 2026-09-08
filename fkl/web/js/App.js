/** Top-level component.
 *
 *   Sidebar (nav, counts, pipeline status) + the active view. An empty corpus
 *   is not a special case: Documents renders its dropzone and zeroed counts.
 *
 *   The old top chrome (header bar + breadcrumb strip + metric strip) folded
 *   into the sidebar, so a view owns the whole canvas to the right of it.
 *
 *   A global drag-over/drop handler intercepts any file dropped anywhere
 *   on the page and forwards it to the API.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { html } from './lib/html.js';
import { api } from './lib/api.js';
import { useJobs, useStats } from './lib/hooks.js';
import { Sidebar } from './components/Sidebar.js';
import { Footer } from './components/Footer.js';
import { RelationsView } from './views/Relations.js';
import { ClaimsView } from './views/Claims.js';
import { ReviewView } from './views/Review.js';
import { DocumentsView } from './views/Documents.js';

/** Full-window drop overlay + handler. */
function useGlobalDrop(onFile) {
  const [dragging, setDragging] = useState(false);
  const depthRef = useRef(0);

  useEffect(() => {
    const carries = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    const onEnter = (e) => { if (carries(e)) { depthRef.current += 1; setDragging(true); } };
    const onOver = (e) => { if (carries(e)) e.preventDefault(); };
    const onLeave = () => {
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setDragging(false);
    };
    const onDrop = async (e) => {
      if (!carries(e)) return;
      e.preventDefault();
      depthRef.current = 0;
      setDragging(false);
      const files = [...e.dataTransfer.files]
        .filter((f) => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'));
      for (const f of files) {
        try { await api.uploadDocument(f); }
        catch { /* ignore per-file */ }
      }
      if (files.length) onFile?.();
    };
    window.addEventListener('dragenter', onEnter);
    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragenter', onEnter);
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [onFile]);

  return dragging;
}

const DropOverlay = ({ visible }) => visible && html`
  <div className="fixed inset-0 z-[100] grid place-items-center bg-background/85 backdrop-blur-sm">
    <div className="flex flex-col items-center gap-space-md px-space-xl py-space-xl min-w-[380px]
                     rounded-xl border border-primary-container/60 bg-surface-container text-center">
      <div className="w-12 h-12 rounded-full bg-primary-container/15 flex items-center justify-center">
        <span className="material-symbols-outlined text-primary-container text-[26px]">
          arrow_upward
        </span>
      </div>
      <div className="font-headline-lg text-headline-lg text-on-surface">Drop to ingest</div>
      <div className="font-body-sm text-body-sm text-on-surface-variant">
        PDF · about a second per page
      </div>
    </div>
  </div>`;

export const App = () => {
  const { data: stats, refresh: refreshStats } = useStats();
  const jobs = useJobs(false).data || [];
  const [tab, setTab] = useState('documents');

  const bumpAll = useCallback(() => { refreshStats(); }, [refreshStats]);
  const dragging = useGlobalDrop(bumpAll);

  const pickFile = () => document.querySelector('input[type=file]')?.click();
  const openUpload = () => {
    setTab('documents');
    setTimeout(pickFile, 60);
  };

  if (!stats) {
    return html`
      <main className="w-full min-h-screen flex items-center justify-center grid-backdrop">
        <div className="w-8 h-8 rounded-full border-2 border-primary-container
                         border-t-transparent animate-spin"></div>
      </main>
      <${DropOverlay} visible=${dragging} />`;
  }

  const k = stats.relations_by_kind || {};
  const relationsTotal = Object.entries(k)
    .filter(([kind]) => kind !== 'unrelated')
    .reduce((n, [, c]) => n + c, 0);
  const counts = {
    documents: stats.documents ?? 0,
    relations: relationsTotal,
    claims: stats.claims ?? 0,
    review: (stats.claims_needing_review ?? 0) + (stats.claims_quarantined ?? 0),
  };
  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued').length;

  return html`
    <${Sidebar} tab=${tab} counts=${counts} running=${running}
                 onTabChange=${setTab} onUploadClick=${openUpload} />
    <div className="lg:pl-sidebar-width min-h-screen flex flex-col grid-backdrop">
      <main className="flex-1 pt-[104px] lg:pt-0">
        ${tab === 'documents' && html`<${DocumentsView} onUploaded=${bumpAll} />`}
        ${tab === 'relations' && html`<${RelationsView} />`}
        ${tab === 'claims'    && html`<${ClaimsView} />`}
        ${tab === 'review'    && html`<${ReviewView} />`}
      </main>
      <${Footer} />
    </div>
    <${DropOverlay} visible=${dragging} />`;
};
