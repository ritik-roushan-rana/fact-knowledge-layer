/** PDF drop targets.
 *
 *  `CompactDropzone` is the slim panel at the top of the Documents view.
 */

import { useCallback, useRef, useState } from 'react';
import { html } from '../lib/html.js';
import { api } from '../lib/api.js';

const isPdf = (f) => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf');

async function upload(files, onError, onDone, onStart) {
  const list = [...files];
  const rejected = list.filter((f) => !isPdf(f));
  const accepted = list.filter(isPdf);
  if (rejected.length) {
    onError?.(rejected.length === 1
      ? `"${rejected[0].name}" is not a PDF.`
      : `${rejected.length} files were skipped (not PDFs).`);
  }
  // Sending the bytes can take a moment on a big file, and the job does not
  // exist until the server answers — so say so before the wait, not after.
  if (accepted.length) onStart?.(accepted.map((f) => f.name));
  for (const file of accepted) {
    try { await api.uploadDocument(file); }
    catch (e) { onError?.(`Could not upload "${file.name}": ${e.message}`); }
  }
  if (accepted.length) onDone?.();
}

const ErrorNote = ({ error }) => error && html`
  <div className="mt-space-sm font-body-sm text-body-sm text-v-contra bg-v-contra/10
                   border border-v-contra/40 rounded-md px-space-md py-space-sm">
    ${error}
  </div>`;

/** Shared drag/drop plumbing: click, drag-over state, and upload. */
function useDropTarget(onDone, onStart) {
  const inputRef = useRef(null);
  const [error, setError] = useState(null);
  const [active, setActive] = useState(false);
  const handleFiles = useCallback(
    (files) => upload(files, setError, onDone, onStart), [onDone, onStart]);

  const bind = {
    onClick: () => inputRef.current?.click(),
    onDragOver: (e) => { e.preventDefault(); setActive(true); },
    onDragEnter: (e) => { e.preventDefault(); setActive(true); },
    onDragLeave: () => setActive(false),
    onDrop: (e) => {
      // The window-level handler in App catches files dropped anywhere on the
      // page. Without this the drop is handled twice and the file uploads
      // twice — once here, once there.
      e.preventDefault();
      e.stopPropagation();
      setActive(false);
      handleFiles(e.dataTransfer.files);
    },
  };
  const input = html`
    <input ref=${inputRef} type="file" accept=".pdf,application/pdf" hidden multiple
           onChange=${(e) => { handleFiles(e.target.files); e.target.value = ''; }} />`;

  return { bind, input, active, error, open: () => inputRef.current?.click() };
}

/* ────────────────────────── Compact variant ────────────────────────── */

export const CompactDropzone = ({ onDone, onStart }) => {
  const { bind, input, active, error } = useDropTarget(onDone, onStart);
  return html`
    <div className="w-full">
      <div ...${bind}
           className=${`flex flex-col sm:flex-row sm:items-center gap-space-md px-space-lg
                         py-space-md rounded-lg border border-dashed cursor-pointer
                         transition-colors
                         ${active
                           ? 'border-primary-container bg-primary-container/[0.06]'
                           : 'border-outline-variant bg-surface-container-low hover:border-outline'}`}>
        ${input}
        <span className="material-symbols-outlined text-primary-container text-[22px]">
          upload_file
        </span>
        <div className="flex flex-col min-w-0 flex-1">
          <span className="font-label-lg text-label-lg text-on-surface">Add a PDF</span>
          <span className="font-body-sm text-body-sm text-on-surface-variant">
            Drop it here or click to browse — multi-column layouts and tables included.
          </span>
        </div>
        <span className="font-label-sm text-label-sm text-outline shrink-0">Max 120 MB</span>
      </div>
      <${ErrorNote} error=${error} />
    </div>`;
};
