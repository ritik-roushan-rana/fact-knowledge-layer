/** Shared page furniture.
 *
 *  `PageHead` is the one title treatment every view uses: a heading, a plain
 *  sentence underneath, and an optional slot for actions on the right.
 *
 *  `StatRow` is the replacement for the old seven-tile metric strip — the same
 *  numbers as a single quiet row of label/value pairs, no boxes.
 */

import { html } from '../lib/html.js';
import { commaNum } from '../lib/format.js';

export const PageHead = ({ title, subtitle, actions }) => html`
  <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-space-md">
    <div className="flex flex-col gap-1 min-w-0">
      <h1 className="font-headline-xl text-headline-xl text-on-surface">${title}</h1>
      ${subtitle && html`
        <p className="font-body-md text-body-md text-on-surface-variant">${subtitle}</p>`}
    </div>
    ${actions && html`<div className="flex items-center gap-space-sm shrink-0">${actions}</div>`}
  </header>`;

const TONE_TEXT = {
  neutral: 'text-on-surface',
  corr:    'text-v-corr',
  contra:  'text-v-contra',
  recon:   'text-v-recon',
  super:   'text-v-super',
  under:   'text-v-under',
};

export const StatRow = ({ items = [] }) => html`
  <div className="flex flex-wrap items-center gap-x-space-xl gap-y-space-md
                   rounded-lg border border-line bg-surface-container-low px-space-lg py-space-md">
    ${items.map(([label, value, tone]) => html`
      <div key=${label} className="flex flex-col gap-0.5 min-w-[92px]">
        <span className="font-label-sm text-label-sm text-outline">${label}</span>
        <span className=${`font-mono text-data-lg tabular
                            ${value ? (TONE_TEXT[tone] || TONE_TEXT.neutral) : 'text-outline'}`}>
          ${typeof value === 'number' ? commaNum(value) : value}
        </span>
      </div>`)}
  </div>`;
