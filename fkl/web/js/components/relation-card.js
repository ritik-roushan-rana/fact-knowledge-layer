/**
 * A verdict about two claims.
 *
 * Ordered the way the engine decides: what it concluded, how confident it is,
 * the two claims side by side, the explanation, and finally the rules that
 * actually fired.
 */

import { html } from '../core/dom.js';
import { badge, meter } from './primitives.js';
import { claimPanel } from './claim-panel.js';

function decidedBy(relation) {
  const pp = relation.value_delta?.percentage_point_difference;
  const source = relation.decided_by === 'llm' ? 'LLM adjudication' : 'Deterministic rules';
  return pp !== undefined ? `${source} · ${pp} pp apart` : source;
}

function contextDiff(diff) {
  const entries = Object.entries(diff || {});
  if (!entries.length) return '';
  return html`<div class="context-diff">${entries.map(([field, [a, b]]) => html`
    <span class="context-diff__item">${field}: ${a ?? '—'} vs ${b ?? '—'}</span>`)}</div>`;
}

export function relationCard(relation) {
  const steps = relation.reasoning_trace || [];
  return html`
    <article class="card relation">
      <header class="card__head">
        ${badge(relation.kind)}
        <div class="meters">
          ${meter('confidence', relation.confidence)}
          ${meter('match', relation.match_confidence)}
          ${meter('verdict', relation.relationship_confidence)}
        </div>
        <span class="relation__decided push">${decidedBy(relation)}</span>
      </header>

      <div class="claim-pair">
        ${claimPanel(relation.claim_a)}
        ${claimPanel(relation.claim_b)}
      </div>

      <div class="explanation">
        <span class="explanation__label">Why</span>${relation.explanation}
        ${contextDiff(relation.context_diff)}
      </div>

      <details class="disclosure">
        <summary>Reasoning trace · ${steps.length} steps</summary>
        <ol class="trace">${steps.map((step) => html`<li class="trace__step">${step}</li>`)}</ol>
      </details>
    </article>`;
}
