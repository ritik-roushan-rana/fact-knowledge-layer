/** Cross-document relationships — the primary view. */

import { api } from '../core/api.js';
import { html } from '../core/dom.js';
import { VERDICTS, verdictLabel } from '../core/format.js';
import { relationCard } from '../components/relation-card.js';
import { empty, verdictDot } from '../components/primitives.js';

function filterBar(activeKind, counts, shown) {
  const available = VERDICTS.filter((kind) => counts[kind]);
  return html`
    <div class="toolbar">
      <div class="chips" role="group" aria-label="Filter by relationship type">
        <button class="chip" type="button" data-kind=""
                aria-pressed="${activeKind === '' ? 'true' : 'false'}">All</button>
        ${available.map((kind) => html`
          <button class="chip" type="button" data-kind="${kind}"
                  aria-pressed="${activeKind === kind ? 'true' : 'false'}">
            ${verdictDot(kind)}${verdictLabel(kind)}
            <span class="chip__count">${counts[kind]}</span>
          </button>`)}
      </div>
      <span class="muted num push" style="font-size:var(--t-xs)">${shown} shown</span>
      <button class="btn btn--ghost" type="button" id="rebuild-relations">Rebuild all</button>
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
      documents.length < 2 ? 'Nothing to compare yet' : 'No relationships of this type',
      documents.length < 2
        ? 'Relationships are found between documents. Upload a second PDF that covers overlapping facts.'
        : 'Clear the filter to see other verdicts, or rebuild after changing thresholds.',
      documents.length < 2 ? '⇄' : '⌕',
    )}`;
  }
  return html`${bar}<div class="relation-list">${relations.map(relationCard)}</div>`;
}
