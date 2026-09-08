/** A verdict about two claims, laid out as a comparison rather than as two
 *  paragraphs side by side.
 *
 *  The two claims almost always share a subject and predicate — that goes in
 *  the card title, once — so the body only has to carry what actually differs:
 *  the value, then one row per context field, with differing rows marked.
 *
 *      [Reconciled]                              confidence 0.75 · R50
 *      Delhivery — adjusted ebitda margin
 *      ┌──────────┬─────────────────┬─────────────────┐
 *      │          │ annual-report   │ q4-presentation │
 *      │ Value    │ 0.93%           │ 1.0%            │
 *      │ Period   │ March 31, 2024  │ Q4 FY24         │  ← differs
 *      └──────────┴─────────────────┴─────────────────┘
 *      Why this reading
 *      › Reasoning trace   › What would change this?
 */

import { useEffect, useRef, useState } from 'react';
import { html } from '../lib/html.js';
import { api } from '../lib/api.js';
import { CONTEXT_FIELDS, evidenceText, pageOf, score, toneStyle, verdictLabel } from '../lib/format.js';

const RULE_TAG_RE = /^\[([A-Z]\d+)\]\s*/;

function parseStep(text) {
  const m = RULE_TAG_RE.exec(text);
  if (m) return { tag: m[1], body: text.slice(m[0].length) };
  const colon = text.indexOf(':');
  if (colon > 0 && colon <= 22) {
    const head = text.slice(0, colon).trim();
    return { tag: head.replace(/[^a-z0-9]+/gi, '_').toLowerCase(), body: text.slice(colon + 1).trim() };
  }
  return { tag: '·', body: text };
}

/** Context fields either claim states, paired up and marked when they differ. */
function contextRows(a, b) {
  return CONTEXT_FIELDS
    .map(([key, label]) => [label, a[`ctx_${key}`], b[`ctx_${key}`]])
    .filter(([, va, vb]) => va || vb)
    .map(([label, va, vb]) => ({
      label: label.charAt(0).toUpperCase() + label.slice(1),
      a: va, b: vb, differs: (va || '') !== (vb || ''),
    }));
}

const shortName = (name = '') => name.replace(/\.pdf$/i, '');

/* ─────────────────────────── Grid cells ─────────────────────────── */

const CELL = 'px-space-md py-space-sm min-w-0';

/** Three cells of one grid row. Rows whose two sides disagree are banded and
 *  get a coloured label, so what differs is findable without reading. */
const Row = ({ label, differs, tone, a, b, cell = '' }) => {
  const band = differs ? 'bg-surface-container/60' : '';
  return [
    html`<div key="l" className=${`${CELL} ${band} font-label-sm text-label-sm
                                    ${differs ? tone.text : 'text-outline'}`}>${label}</div>`,
    html`<div key="a" className=${`${CELL} ${band} ${cell}`}>${a}</div>`,
    html`<div key="b" className=${`${CELL} ${band} ${cell}`}>${b}</div>`,
  ];
};

/* ─────────────────────── Source column header ─────────────────────── */

const SourceCell = ({ claim }) => html`
  <div className="flex flex-col gap-0.5 min-w-0">
    <span className="font-label-md text-label-md text-on-surface truncate"
          title=${claim.source_document}>
      ${shortName(claim.source_document)}
    </span>
    <span className="font-mono text-code-sm text-outline">
      p.${pageOf(claim)} · ${claim.origin === 'table' ? 'table' : 'sentence'}
    </span>
  </div>`;

/* ─────────────────────── Evidence + PDF preview ─────────────────────── */

const Evidence = ({ claim }) => {
  const [open, setOpen] = useState(false);
  const hasPreview = claim.bbox && claim.claim_id;
  return html`
    <div className="flex flex-col gap-space-xs">
      <p className="font-body-sm text-body-sm text-on-surface-variant line-clamp-2"
         title=${evidenceText(claim)}>
        ${evidenceText(claim)}
      </p>
      <div className="flex items-center gap-space-md font-label-sm text-label-sm text-outline">
        <span className="font-mono">
          grounding ${score(claim.grounding_score)}
        </span>
        ${hasPreview && html`
          <button type="button" onClick=${() => setOpen(!open)}
                  className="hover:text-primary-container transition-colors">
            ${open ? 'Hide source' : 'View in source PDF'}
          </button>`}
      </div>
      ${open && hasPreview && html`
        <div className="mt-space-xs p-space-sm rounded-md bg-surface-container-lowest border border-line">
          <img loading="lazy" src=${`/api/claims/${claim.claim_id}/preview.png?zoom=crop`}
               alt=${`Page ${pageOf(claim)} of ${claim.source_document}`}
               className="max-w-full h-auto rounded-md bg-white" />
          <a href=${`/api/claims/${claim.claim_id}/preview.png?zoom=page`}
             target="_blank" rel="noopener"
             className="mt-space-xs inline-block font-label-sm text-label-sm text-outline
                         hover:text-primary-container">
            Open the whole page
          </a>
        </div>`}
    </div>`;
};

/* ─────────────────────── Expandable sections ─────────────────────── */

const Expandable = ({ label, hint, children }) => {
  const [open, setOpen] = useState(false);
  return html`
    <div className="flex flex-col">
      <button type="button" onClick=${() => setOpen(!open)}
              className="self-start flex items-center gap-1.5 font-label-md text-label-md
                          text-on-surface-variant hover:text-on-surface transition-colors">
        <span className=${`material-symbols-outlined text-[16px] transition-transform
                            ${open ? 'rotate-90' : ''}`}>arrow_right</span>
        ${label}
        ${hint && html`<span className="text-outline hidden sm:inline">· ${hint}</span>`}
      </button>
      ${open && html`<div className="pt-space-md pl-space-lg">${children}</div>`}
    </div>`;
};

const Trace = ({ relation, tone }) => {
  const steps = relation.reasoning_trace || [];
  const source = relation.decided_by === 'llm' ? 'adjudicated by model' : 'deterministic rules';
  return html`
    <${Expandable} label=${`Reasoning trace · ${steps.length} steps`} hint=${source}>
      <ol className="flex flex-col gap-space-sm font-body-sm text-body-sm text-on-surface-variant">
        ${steps.length === 0 && html`<li className="italic">No reasoning steps recorded.</li>`}
        ${steps.map((step, i) => {
          const p = parseStep(step);
          const isLast = i === steps.length - 1;
          return html`
            <li key=${i} className="flex items-start gap-space-md">
              <span className=${`shrink-0 w-[92px] font-mono text-code-sm truncate
                                  ${isLast ? tone.text : 'text-on-surface'}`}>
                ${p.tag}
              </span>
              <span className="min-w-0">${p.body}</span>
            </li>`;
        })}
      </ol>
    </Expandable>`;
};

const Counterfactuals = ({ relationId }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const nodeRef = useRef(null);
  const loadedRef = useRef(false);

  useEffect(() => {
    if (!nodeRef.current || loadedRef.current) return undefined;
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !loadedRef.current) {
        loadedRef.current = true;
        io.disconnect();
        api.counterfactuals(relationId)
          .then(setData)
          .catch((e) => setError(e.message));
      }
    }, { rootMargin: '150px 0px' });
    io.observe(nodeRef.current);
    return () => io.disconnect();
  }, [relationId]);

  return html`
    <div ref=${nodeRef}>
      <${Expandable} label="What would change this?" hint="one qualifier at a time">
        <div className="flex flex-col gap-space-sm font-body-sm text-body-sm text-on-surface-variant">
          ${error && html`<span className="text-v-contra">Could not load: ${error}</span>`}
          ${!error && !data && html`<span className="italic">Loading…</span>`}
          ${data && data.counterfactuals.length === 0 && html`
            <span>No single qualifier changes the outcome — every stated context field on this
              pair points the same way.</span>`}
          ${data && data.counterfactuals.length > 0 && data.counterfactuals.map((c, i) => {
            const scopeText = c.removed_from.length === 2
              ? 'on both sides'
              : `on side ${c.removed_from.join(', ')}`;
            return html`
              <div key=${i} className="flex items-start gap-space-md">
                <span className="font-mono text-code-sm text-outline shrink-0">${i + 1}</span>
                <span>
                  Without <b className="text-on-surface">${c.ablated_field}</b> ${scopeText}, this
                  would read <b className="text-on-surface">${c.would_be_kind}</b>${' — '}
                  ${c.explanation}
                </span>
              </div>`;
          })}
        </div>
      </Expandable>
    </div>`;
};

/* ───────────────────────────── Card ───────────────────────────── */

export const RelationCard = ({ relation }) => {
  const tone = toneStyle(relation.kind);
  const a = relation.claim_a;
  const b = relation.claim_b;
  if (!a || !b) return null;

  const sameStatement = a.subject === b.subject && a.predicate === b.predicate;
  const rows = contextRows(a, b);
  const pp = relation.value_delta?.percentage_point_difference;

  return html`
    <article className="relative rounded-xl border border-line bg-surface-container-low
                          overflow-hidden hover:border-line-strong transition-colors">
      <div className=${`absolute left-0 top-0 bottom-0 w-[3px] ${tone.rail}`}></div>

      <div className="p-space-lg pl-space-xl flex flex-col gap-space-lg">
        <!-- Verdict + what this pair is about -->
        <div className="flex flex-col gap-space-sm">
          <div className="flex flex-wrap items-center justify-between gap-space-sm">
            <span className=${`h-6 px-space-md rounded-full ${tone.soft} font-label-md text-label-md
                                font-semibold inline-flex items-center`}>
              ${verdictLabel(relation.kind)}
            </span>
            <span className="font-mono text-code-sm text-outline">
              confidence ${score(relation.confidence)}
              ${relation.rule_id && html`
                <span className=${`ml-space-sm ${tone.text}`}>${relation.rule_id}</span>`}
            </span>
          </div>
          ${sameStatement && html`
            <h3 className="font-headline-md text-headline-md text-on-surface">
              ${a.subject} <span className="text-outline">—</span>${' '}
              <span className="text-on-surface-variant font-normal">${a.predicate}</span>
            </h3>`}
        </div>

        <!-- The comparison -->
        <div className="overflow-x-auto -mx-space-sm px-space-sm">
          <div className="min-w-[520px] grid grid-cols-[88px_minmax(0,1fr)_minmax(0,1fr)]
                           rounded-lg border border-line bg-surface-container-lowest/60">
            <!-- Which document each column is -->
            <div className=${CELL}></div>
            <div className=${CELL}><${SourceCell} claim=${a} /></div>
            <div className=${CELL}><${SourceCell} claim=${b} /></div>

            ${!sameStatement && html`
              <${Row} label="Claim" differs=${true} tone=${tone}
                       cell="font-body-sm text-body-sm text-on-surface"
                       a=${`${a.subject} — ${a.predicate}`}
                       b=${`${b.subject} — ${b.predicate}`} />`}

            <${Row} label="Value" differs=${true} tone=${tone}
                     cell=${`font-mono text-data-lg tabular ${tone.text}`}
                     a=${a.value} b=${b.value} />

            ${rows.map((r) => html`
              <${Row} key=${r.label} label=${r.label} differs=${r.differs} tone=${tone}
                       cell=${`font-body-sm text-body-sm
                               ${r.differs ? 'text-on-surface' : 'text-on-surface-variant'}`}
                       a=${r.a || '—'} b=${r.b || '—'} />`)}

            <${Row} label="Evidence" tone=${tone}
                     a=${html`<${Evidence} claim=${a} />`}
                     b=${html`<${Evidence} claim=${b} />`} />
          </div>
        </div>

        <!-- Why the verdict reads this way -->
        <p className="font-body-md text-body-md text-on-surface-variant max-w-3xl">
          ${relation.explanation}
          ${pp !== undefined && pp !== null && html`
            ${' '}<span className=${`${tone.text}`}>(${pp} pp apart)</span>`}
        </p>

        <div className="flex flex-col gap-space-md pt-space-xs border-t border-line">
          <${Trace} relation=${relation} tone=${tone} />
          <${Counterfactuals} relationId=${relation.relation_id} />
        </div>
      </div>
    </article>`;
};
