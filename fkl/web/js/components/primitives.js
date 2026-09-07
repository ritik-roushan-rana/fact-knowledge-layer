/** Small presentational pieces shared across views. */

import { html } from '../core/dom.js';
import { percent, score, verdictLabel } from '../core/format.js';

export const badge = (kind) => html`
  <span class="badge badge--${kind}">${verdictLabel(kind)}</span>`;

export const pill = (text) => html`<span class="pill">${text}</span>`;

export const meter = (label, value) => html`
  <span class="meter">${label}
    <span class="meter__track"><i class="meter__fill" style="width:${percent(value)}%"></i></span>
    <b class="meter__value">${score(value)}</b>
  </span>`;

export const confidenceBar = (value) => html`
  <span class="confbar">
    <span class="confbar__track"><i class="confbar__fill" style="width:${percent(value)}%"></i></span>
    ${score(value)}
  </span>`;

export const empty = (title, detail = '') => html`
  <div class="empty"><div class="empty__title">${title}</div>${detail}</div>`;

export const skeleton = (count = 2) => html`
  ${Array.from({ length: count }, () => html`<div class="skeleton"></div>`)}`;

export const errorState = (message) => html`
  <div class="empty">
    <div class="empty__title" style="color:var(--bad)">Could not load</div>${message}
  </div>`;
