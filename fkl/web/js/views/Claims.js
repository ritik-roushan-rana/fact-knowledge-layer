/** Claims — the full ledger of grounded facts.
 *
 *  Hundreds of rows, so the list is built for scanning: one line per claim,
 *  values right-aligned in a column of their own, and a coloured dot for
 *  status. The evidence, scores and source page live behind the row — click
 *  one open and it expands in place.
 */

import { useMemo, useState } from 'react';
import { html } from '../lib/html.js';
import { commaNum, pageOf } from '../lib/format.js';
import { useClaims, useDocuments, useStats } from '../lib/hooks.js';
import { ClaimList } from '../components/ClaimList.js';
import { PageHead } from '../components/PageHead.js';

/* ─────────────── Filter row ─────────────── */

const SELECT = 'h-9 px-space-sm bg-surface-container border border-line rounded-md '
             + 'text-on-surface font-label-md text-label-md cursor-pointer '
             + 'focus:outline-none focus:border-primary-container';

const Toggle = ({ label, count, active, onClick }) => html`
  <button type="button" onClick=${onClick}
          className=${`h-8 px-space-md rounded-full border font-label-md text-label-md
                        flex items-center gap-space-xs transition-colors whitespace-nowrap
                        ${active
                          ? 'bg-surface-container-high border-line-strong text-on-surface'
                          : 'bg-transparent border-line text-on-surface-variant hover:bg-surface-container'}`}>
    ${label}
    ${count !== undefined && html`
      <span className="font-mono text-code-sm text-outline tabular">${commaNum(count)}</span>`}
  </button>`;

const FilterRow = ({
  search, onSearch, docId, onDoc, documents,
  origin, onOrigin, originCounts, shown, sort, onSort,
}) => html`
  <div className="flex flex-col gap-space-sm">
    <div className="flex flex-wrap items-center gap-space-sm">
      <div className="relative flex-1 min-w-[240px]">
        <span className="material-symbols-outlined absolute left-2.5 top-1/2 -translate-y-1/2
                          text-outline text-[18px] pointer-events-none">search</span>
        <input type="search" value=${search} onInput=${(e) => onSearch(e.target.value)}
               placeholder="Search subject, predicate, or value"
               className="w-full h-9 pl-9 pr-space-md rounded-md bg-surface-container border
                           border-line text-on-surface placeholder:text-outline font-body-md
                           text-body-md focus:outline-none focus:border-primary-container
                           transition-colors" />
      </div>
      <select value=${docId} onChange=${(e) => onDoc(e.target.value)}
              className=${`${SELECT} max-w-[260px] truncate`}>
        <option value="">All documents (${documents.length})</option>
        ${documents.map((d) => html`
          <option key=${d.doc_id} value=${d.doc_id}>${d.filename}</option>`)}
      </select>
    </div>

    <div className="flex flex-wrap items-center gap-space-xs">
      <${Toggle} label="All"      active=${origin === ''}         onClick=${() => onOrigin('')} />
      <${Toggle} label="Sentence" count=${originCounts.sentence} active=${origin === 'sentence'}
                  onClick=${() => onOrigin('sentence')} />
      <${Toggle} label="Table"    count=${originCounts.table}    active=${origin === 'table'}
                  onClick=${() => onOrigin('table')} />
      <div className="ml-auto flex items-center gap-space-md">
        <span className="font-label-sm text-label-sm text-outline">${commaNum(shown)} shown</span>
        <select value=${sort} onChange=${(e) => onSort(e.target.value)} className=${SELECT}>
          <option value="grounding">Best grounded</option>
          <option value="extraction">Best extracted</option>
          <option value="page">By page</option>
        </select>
      </div>
    </div>
  </div>`;

/* ─────────────── The view ─────────────── */

export const ClaimsView = () => {
  const [pending, setPending] = useState('');
  const [search, setSearch] = useState('');
  const [docId, setDocId] = useState('');
  const [origin, setOrigin] = useState('');
  const [sort, setSort] = useState('grounding');

  // Server-side query on submit. We keep a locally-controlled `pending`
  // input so we do not fire a request on every keystroke.
  const submitTimer = useMemo(() => ({ id: 0 }), []);
  const onSearchInput = (v) => {
    setPending(v);
    clearTimeout(submitTimer.id);
    submitTimer.id = setTimeout(() => setSearch(v), 300);
  };

  const stats = useStats().data || {};
  const { data: documents = [] } = useDocuments();
  const filters = { search, doc_id: docId };
  if (origin) filters.origin = origin;
  const { data: rawClaims = [], loading } = useClaims(filters);

  const claims = useMemo(() => {
    const list = [...rawClaims];
    list.sort((a, b) => {
      if (sort === 'extraction') return (b.extraction_confidence || 0) - (a.extraction_confidence || 0);
      if (sort === 'page')       return (pageOf(a) || 0) - (pageOf(b) || 0);
      return (b.grounding_score || 0) - (a.grounding_score || 0);
    });
    return list;
  }, [rawClaims, sort]);

  const originCounts = useMemo(() => {
    const table = rawClaims.filter((c) => c.origin === 'table').length;
    return { table, sentence: rawClaims.length - table };
  }, [rawClaims]);

  const filtered = !!(search || docId || origin);

  return html`
    <div className="w-full max-w-5xl mx-auto px-space-lg py-space-xl flex flex-col gap-space-lg">
      <${PageHead}
        title="Claims"
        subtitle=${`${commaNum(stats.claims ?? 0)} grounded facts across `
          + `${documents.length} ${documents.length === 1 ? 'document' : 'documents'}`} />

      <${FilterRow}
         search=${pending} onSearch=${onSearchInput}
         docId=${docId} onDoc=${setDocId} documents=${documents}
         origin=${origin} onOrigin=${setOrigin} originCounts=${originCounts}
         shown=${claims.length} sort=${sort} onSort=${setSort} />

      ${loading && html`
        <div className="flex flex-col gap-space-sm">
          ${[1, 2, 3, 4, 5, 6].map((i) => html`
            <div key=${i} className="h-11 rounded-md bg-surface-container-low animate-pulse"></div>`)}
        </div>`}

      ${!loading && claims.length === 0 && html`
        <div className="rounded-lg border border-line bg-surface-container-low px-space-lg
                          py-space-xl flex flex-col items-center text-center gap-space-sm">
          <span className="material-symbols-outlined text-[24px] text-outline">
            ${filtered ? 'search_off' : 'inbox'}
          </span>
          <div className="font-headline-md text-headline-md text-on-surface">
            ${filtered ? 'No claims match' : 'No claims yet'}
          </div>
          <p className="font-body-sm text-body-sm text-on-surface-variant max-w-sm">
            ${filtered
              ? 'Adjust the filter, or clear it to browse the whole ledger.'
              : 'Add a PDF and its facts appear here.'}
          </p>
        </div>`}

      ${!loading && claims.length > 0 && html`<${ClaimList} claims=${claims} />`}
    </div>`;
};
