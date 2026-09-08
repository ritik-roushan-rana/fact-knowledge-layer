/** Presentation-level formatting helpers. No DOM, no fetching.
 *
 *   Verdict → tone → concrete Tailwind class tokens. Six semantic tones map
 *   to the seven verdict kinds:
 *
 *     corr    #3ddc84  corroboration           (agreement)
 *     contra  #ff5c5c  contradiction           (rejected)
 *     recon   #f5a623  reconciled              (context-resolved)
 *     super   #4da3ff  supersedes / part-of    (temporal / hierarchy)
 *     partial #8a8f98  partial_cover           (incomplete overlap)
 *     under   #a78bfa  underspecified          (insufficient context)
 */

export const VERDICTS = [
  'contradiction', 'reconciled', 'corroboration', 'supersedes',
  'component_of_total', 'partial_cover', 'underspecified',
];

const VERDICT_LABELS = {
  contradiction: 'Contradicts',
  reconciled: 'Reconciled',
  corroboration: 'Corroborates',
  supersedes: 'Supersedes',
  component_of_total: 'Component of total',
  partial_cover: 'Partial cover',
  underspecified: 'Underspecified',
  unrelated: 'Unrelated',
};

const VERDICT_MEANINGS = {
  contradiction: 'The documents disagree and context does not explain it',
  reconciled: 'Values differ, but a difference in context accounts for it',
  corroboration: 'The documents agree',
  supersedes: 'The same measurement, revised by a later figure',
  component_of_total: 'The narrower claim is a plausible part of the broader one',
  partial_cover: 'Related properties, but no numeric part-of check applied',
  underspecified: 'Not enough context stated to judge',
  unrelated: 'Not the same property after all',
};

export const verdictLabel = (k) => VERDICT_LABELS[k] || k;
export const verdictMeaning = (k) => VERDICT_MEANINGS[k] || '';

/** Verdict → tone bucket. */
const TONES = {
  corroboration: 'corr',
  contradiction: 'contra',
  reconciled: 'recon',
  supersedes: 'super',
  component_of_total: 'super',
  partial_cover: 'partial',
  underspecified: 'under',
  unrelated: 'partial',
};
export const toneOf = (k) => TONES[k] || 'partial';

/** Tone → concrete class tokens + raw hex.
 *
 *  `chip`     — outlined verdict chip (transparent fill, coloured border+text)
 *  `soft`     — subtle tinted fill for count badges and section highlights
 *  `text`     — bare text colour
 *  `border`   — bare border colour
 *  `dot`      — solid dot / square (bg)
 *  `rail`     — 3px left accent rail (bg)
 *  `hex`      — raw hex for inline styles (borderLeftColor etc.)
 */
const TONE_STYLES = {
  corr: {
    text: 'text-v-corr', border: 'border-v-corr', dot: 'bg-v-corr', rail: 'bg-v-corr',
    chip: 'text-v-corr border border-v-corr bg-transparent',
    soft: 'text-v-corr bg-v-corr/10',
    hex: '#3ddc84',
  },
  contra: {
    text: 'text-v-contra', border: 'border-v-contra', dot: 'bg-v-contra', rail: 'bg-v-contra',
    chip: 'text-v-contra border border-v-contra bg-transparent',
    soft: 'text-v-contra bg-v-contra/10',
    hex: '#ff5c5c',
  },
  recon: {
    text: 'text-v-recon', border: 'border-v-recon', dot: 'bg-v-recon', rail: 'bg-v-recon',
    chip: 'text-v-recon border border-v-recon bg-transparent',
    soft: 'text-v-recon bg-v-recon/10',
    hex: '#f5a623',
  },
  super: {
    text: 'text-v-super', border: 'border-v-super', dot: 'bg-v-super', rail: 'bg-v-super',
    chip: 'text-v-super border border-v-super bg-transparent',
    soft: 'text-v-super bg-v-super/10',
    hex: '#4da3ff',
  },
  partial: {
    text: 'text-v-partial', border: 'border-v-partial', dot: 'bg-v-partial', rail: 'bg-v-partial',
    chip: 'text-v-partial border border-v-partial bg-transparent',
    soft: 'text-v-partial bg-v-partial/10',
    hex: '#8a8f98',
  },
  under: {
    text: 'text-v-under', border: 'border-v-under', dot: 'bg-v-under', rail: 'bg-v-under',
    chip: 'text-v-under border border-v-under bg-transparent',
    soft: 'text-v-under bg-v-under/10',
    hex: '#a78bfa',
  },
};
export const toneStyle = (k) => TONE_STYLES[toneOf(k)] || TONE_STYLES.partial;

export const percent = (v) => Math.round((Number(v) || 0) * 100);
export const score = (v) => (Number(v) || 0).toFixed(2);

/** Context fields a claim can carry, in reading order. */
export const CONTEXT_FIELDS = [
  ['period', 'period'], ['unit', 'unit'], ['scope', 'scope'], ['basis', 'basis'],
  ['geography', 'geography'], ['as_of', 'as of'], ['denominator', 'denominator'],
  ['other', 'qualifier'],
];

export const statedContext = (c) => CONTEXT_FIELDS
  .map(([key, label]) => [label, c[`ctx_${key}`]])
  .filter(([, v]) => v);

/** Which text to show as a claim's evidence. */
export function evidenceText(claim) {
  const located = (claim.matched_text || '').trim();
  const assembled = (claim.span_text || '').trim();
  return (located.length >= 12 || !assembled) ? located : assembled;
}

export const pageOf = (c) => c.grounding_page ?? c.span_page;

export function commaNum(v) {
  return (Number(v) || 0).toLocaleString();
}

/** `ingested_at` comes off the store as epoch seconds (a REAL column).
 *  Tolerate an ISO string too, in case a caller hands one over. */
export function ingestedMs(v) {
  if (v == null || v === '') return 0;
  const n = Number(v);
  if (Number.isFinite(n)) return n * 1000;
  const t = Date.parse(v);
  return Number.isNaN(t) ? 0 : t;
}

/** Epoch seconds → `YYYY-MM-DD`, or '' when there is no usable timestamp. */
export function ingestedDate(v) {
  const ms = ingestedMs(v);
  return ms ? new Date(ms).toISOString().slice(0, 10) : '';
}
