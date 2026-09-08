/** The app's one piece of chrome: a fixed left rail carrying the wordmark,
 *  the upload action, section navigation with live counts, and the pipeline
 *  status. Everything that used to live in three stacked top bars is here,
 *  which leaves the canvas to the right free for content.
 *
 *  Below `lg` the rail folds into a compact top bar with a scrolling nav row.
 */

import { html } from '../lib/html.js';
import { commaNum } from '../lib/format.js';

const SECTIONS = [
  { id: 'documents', label: 'Documents',    icon: 'description' },
  { id: 'relations', label: 'Relationships', icon: 'compare_arrows' },
  { id: 'claims',    label: 'Claims',        icon: 'format_quote' },
  { id: 'review',    label: 'Needs review',  icon: 'flag' },
];

const Wordmark = () => html`
  <div className="flex items-center gap-space-sm min-w-0">
    <div className="w-7 h-7 rounded-md bg-primary-container flex items-center justify-center shrink-0">
      <span className="font-mono text-[12px] font-bold text-on-primary-container">FK</span>
    </div>
    <div className="flex flex-col min-w-0 leading-tight">
      <span className="font-headline-md text-headline-md text-on-surface truncate">Fact Knowledge</span>
      <span className="font-label-sm text-label-sm text-outline">Layer</span>
    </div>
  </div>`;

const UploadButton = ({ onClick, compact }) => html`
  <button type="button" onClick=${onClick}
          className=${`h-9 px-space-md rounded-md bg-primary-container text-on-primary-container
                        font-label-lg text-label-lg font-semibold flex items-center justify-center
                        gap-space-xs hover:bg-primary transition-colors
                        ${compact ? '' : 'w-full'}`}>
    <span className="material-symbols-outlined text-[18px]">add</span>
    <span className=${compact ? 'hidden sm:inline' : ''}>Add PDF</span>
  </button>`;

/** One nav row. Active state is a filled surface plus an amber left marker —
 *  no colour shouting, but unmistakable at a glance. */
const NavItem = ({ section, active, count, onClick, row }) => {
  const base = 'group relative flex items-center gap-space-sm rounded-md transition-colors '
             + 'font-label-lg text-label-lg whitespace-nowrap';
  const tone = active
    ? 'bg-surface-container-high text-on-surface'
    : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface';
  return html`
    <button type="button" onClick=${onClick} aria-current=${active ? 'page' : undefined}
            className=${`${base} ${tone} ${row ? 'h-8 px-space-md shrink-0' : 'h-9 pl-space-sm pr-space-sm w-full'}`}>
      ${!row && html`
        <span className=${`absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-full transition-colors
                            ${active ? 'bg-primary-container' : 'bg-transparent'}`}></span>`}
      ${!row && html`
        <span className=${`material-symbols-outlined text-[18px] ml-space-sm
                            ${active ? 'text-primary-container' : 'text-outline group-hover:text-on-surface-variant'}`}>
          ${section.icon}
        </span>`}
      <span className="flex-1 text-left">${section.label}</span>
      ${count > 0 && html`
        <span className=${`font-mono text-code-sm tabular
                            ${active ? 'text-on-surface' : 'text-outline'}`}>
          ${commaNum(count)}
        </span>`}
    </button>`;
};

const StatusFoot = ({ running }) => html`
  <div className="flex items-center gap-space-sm pt-space-md border-t border-line
                   font-label-sm text-label-sm">
    <span className=${`w-1.5 h-1.5 rounded-full ${running ? 'bg-primary-container animate-pulse' : 'bg-tertiary'}`}></span>
    <span className=${running ? 'text-primary-container' : 'text-on-surface-variant'}>
      ${running ? `Processing ${running} file${running === 1 ? '' : 's'}` : 'Pipeline idle'}
    </span>
  </div>`;

export const Sidebar = ({ tab, counts = {}, running = 0, onTabChange, onUploadClick }) => html`
  <!-- Desktop rail -->
  <aside className="hidden lg:flex fixed inset-y-0 left-0 w-sidebar-width z-40 flex-col
                     gap-space-lg px-space-md py-space-base bg-surface border-r border-line">
    <${Wordmark} />
    <${UploadButton} onClick=${onUploadClick} />
    <nav className="flex flex-col gap-space-2xs" aria-label="Sections">
      ${SECTIONS.map((s) => html`
        <${NavItem} key=${s.id} section=${s} active=${tab === s.id} count=${counts[s.id]}
                     onClick=${() => onTabChange?.(s.id)} />`)}
    </nav>
    <div className="mt-auto"><${StatusFoot} running=${running} /></div>
  </aside>

  <!-- Compact top bar -->
  <header className="lg:hidden fixed top-0 inset-x-0 z-40 bg-surface/95 backdrop-blur border-b border-line">
    <div className="h-14 px-space-base flex items-center justify-between gap-space-sm">
      <${Wordmark} />
      <${UploadButton} onClick=${onUploadClick} compact=${true} />
    </div>
    <nav className="flex items-center gap-space-xs px-space-sm pb-space-sm overflow-x-auto"
         aria-label="Sections">
      ${SECTIONS.map((s) => html`
        <${NavItem} key=${s.id} section=${s} active=${tab === s.id} count=${counts[s.id]}
                     row=${true} onClick=${() => onTabChange?.(s.id)} />`)}
    </nav>
  </header>`;
