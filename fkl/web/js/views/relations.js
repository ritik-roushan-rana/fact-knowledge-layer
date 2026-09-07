/** Cross-document relationships — the primary view. */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { VERDICTS, verdictLabel } from '../core/format.js';
import { relationCard } from '../components/relation-card.js';
import { empty } from '../components/primitives.js';

function filterBar(activeKind, counts, shown) {
  const available = VERDICTS.filter((kind) => counts[kind]);
  return html`
    <div class="toolbar">
      <div class="chips" role="group" aria-label="Filter by relationship type">
        <button class="chip" data-kind="" aria-pressed="${activeKind === '' ? 'true' : 'false'}">
          All</button>
        ${available.map((kind) => html`
          <button class="chip" data-kind="${kind}"
                  aria-pressed="${activeKind === kind ? 'true' : 'false'}">
            ${verdictLabel(kind)}<span class="chip__count">${counts[kind]}</span>
          </button>`)}
      </div>
      <span class="muted num push">${shown} shown</span>
      <button class="btn btn--ghost" id="rebuild-relations">Rebuild all</button>
    </div>`;
}

export async function render(state) {
  const [relations, documents] = await Promise.all([
    api.relations({ kind: state.filters.kind }),
    api.documents(),
  ]);
  const counts = state.stats?.relations_by_kind || {};
  const bar = filterBar(state.filters.kind, counts, relations.length);

  if (!relations.length) {
    return html`${bar}${empty(
      'No relationships of this type',
      documents.length < 2
        ? 'Upload at least two documents that discuss overlapping facts.'
        : 'Try another filter, or rebuild after changing thresholds.',
    )}`;
  }
  return html`${bar}${relations.map(relationCard)}`;
}
