/** One quiet line at the bottom of the canvas. */

import { html } from '../lib/html.js';

export const Footer = () => html`
  <footer className="w-full px-space-lg py-space-md border-t border-line
                      flex flex-wrap items-center justify-between gap-space-sm
                      font-label-sm text-label-sm text-outline">
    <span>Fact Knowledge Layer</span>
    <span>Every claim is traced back to a span in its source PDF.</span>
  </footer>`;
