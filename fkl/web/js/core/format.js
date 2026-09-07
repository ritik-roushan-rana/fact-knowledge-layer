/** Presentation-level formatting. No DOM, no fetching — easy to reason about. */

export const VERDICTS = [
  'contradiction', 'reconciled', 'corroboration',
  'supersedes', 'partial_cover', 'underspecified',
];

const VERDICT_LABELS = {
  contradiction: 'Contradiction',
  reconciled: 'Reconciled',
  corroboration: 'Corroboration',
  supersedes: 'Supersedes',
  partial_cover: 'Partial cover',
  underspecified: 'Underspecified',
  unrelated: 'Unrelated',
};

/**
 * What each verdict means, in one line. Shown next to the badge so a reader
 * does not have to have learned the vocabulary first.
 */
const VERDICT_MEANINGS = {
  contradiction: 'The documents disagree and context does not explain it',
  reconciled: 'Values differ, but a difference in context accounts for it',
  corroboration: 'The documents agree',
  supersedes: 'The same measurement, revised by a later figure',
  partial_cover: 'One claim measures only part of what the other measures',
  underspecified: 'Not enough context stated to judge',
  unrelated: 'Not the same property after all',
};

export const verdictLabel = (kind) => VERDICT_LABELS[kind] || kind;
export const verdictMeaning = (kind) => VERDICT_MEANINGS[kind] || '';

/** Below this, a confidence is shown in the caution tone rather than the accent. */
export const LOW_CONFIDENCE = 0.5;
export const isLow = (value) => Number(value) < LOW_CONFIDENCE;

export const percent = (value) => Math.round((Number(value) || 0) * 100);
export const score = (value) => (Number(value) || 0).toFixed(2);

/** The context fields a claim can carry, in the order they are worth reading. */
export const CONTEXT_FIELDS = [
  ['period', 'period'], ['unit', 'unit'], ['scope', 'scope'], ['basis', 'basis'],
  ['geography', 'geography'], ['as_of', 'as of'], ['denominator', 'denominator'],
  ['other', 'qualifier'],
];

export const statedContext = (claim) => CONTEXT_FIELDS
  .map(([key, label]) => [label, claim[`ctx_${key}`]])
  .filter(([, value]) => value);

/**
 * Which text to show as a claim's evidence.
 *
 * A table claim's *located* text is only the cell — "0.93%" — which reads as
 * nothing. The assembled "row label | column header | cell" is the evidence a
 * person can actually check, so it is used whenever the located text is too
 * short to stand on its own.
 */
export function evidenceText(claim) {
  const located = (claim.matched_text || '').trim();
  const assembled = (claim.span_text || '').trim();
  return (located.length >= 12 || !assembled) ? located : assembled;
}

export const pageOf = (claim) => claim.grounding_page ?? claim.span_page;

export function truncate(text, max = 140) {
  const s = String(text ?? '');
  return s.length > max ? `${s.slice(0, max)}…` : s;
}
