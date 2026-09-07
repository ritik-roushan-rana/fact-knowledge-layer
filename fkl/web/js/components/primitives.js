/**
 * Small presentational pieces shared across views.
 *
 * Anything that appears more than once belongs here rather than as a one-off
 * in a view, so a visual change happens in one place.
 */

import { html } from '../core/dom.js';
import { isLow, percent, score, verdictLabel } from '../core/format.js';

/** Verdict badge. The CSS adds a status dot so it survives greyscale. */
export const badge = (kind) => html`
  <span class="badge badge--${kind}">${verdictLabel(kind)}</span>`;

/** Small monospace tag: page numbers, origin, status. */
export const pill = (text) => html`<span class="pill">${text}</span>`;

/** A coloured dot used to key a filter chip to its verdict colour. */
export const verdictDot = (kind) => html`
  <span class="chip__dot" style="--chip-tone:var(--v-${toneOf(kind)})"></span>`;

const TONES = {
  corroboration: 'affirm', contradiction: 'conflict', reconciled: 'explain',
  supersedes: 'revise', partial_cover: 'partial', underspecified: 'unknown',
};
export const toneOf = (kind) => TONES[kind] || 'partial';

/** Labelled confidence meter. Reads low values in the caution tone. */
export const meter = (label, value) => html`
  <div class="meter ${isLow(value) ? 'meter--low' : ''}">
    <div class="meter__head">
      <span class="meter__label">${label}</span>
      <span class="meter__value">${score(value)}</span>
    </div>
    <div class="meter__track"
         role="meter" aria-label="${label}" aria-valuenow="${percent(value)}"
         aria-valuemin="0" aria-valuemax="100">
      <i class="meter__fill" style="width:${percent(value)}%"></i>
    </div>
  </div>`;

/** Compact inline variant for table rows. */
export const confidenceBar = (value) => html`
  <span class="confbar ${isLow(value) ? 'confbar--low' : ''}">
    <span class="confbar__track"><i class="confbar__fill" style="width:${percent(value)}%"></i></span>
    ${score(value)}
  </span>`;

/** A single figure with a caption; optionally keyed to a verdict tone. */
export const metricTile = (label, value, tone = '') => html`
  <div class="metric ${tone ? `metric--accented metric--${tone}` : ''}">
    <div class="metric__value">${value}</div>
    <div class="metric__label">${label}</div>
  </div>`;

/** Empty state: says what is missing and what to do about it. */
export const empty = (title, detail = '', icon = '◍') => html`
  <div class="empty">
    <div class="empty__icon" aria-hidden="true">${icon}</div>
    <div class="empty__title">${title}</div>
    ${detail ? html`<p class="empty__detail">${detail}</p>` : ''}
  </div>`;

export const skeleton = (count = 2) => html`
  <div class="skeletons">${Array.from({ length: count }, () => html`<div class="skeleton"></div>`)}</div>`;

export const errorState = (message) => html`
  <div class="empty">
    <div class="empty__icon" aria-hidden="true">!</div>
    <div class="empty__title">Could not load</div>
    <p class="empty__detail">${message}</p>
  </div>`;
