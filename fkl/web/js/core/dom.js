/**
 * Rendering helpers.
 *
 * `html` is a tagged template that escapes interpolated values by default and
 * returns a marked result. Because the result is marked, nesting one template
 * inside another composes correctly with no ceremony, while a bare string
 * still gets escaped. That combination is what makes an injection bug hard to
 * write by accident: escaping is the default and safety is not opt-in.
 *
 *   html`<p>${userText}</p>`          // escaped
 *   html`<div>${claimPanel(claim)}</div>`  // nested template, not escaped
 *   html`<ul>${items.map(row)}</ul>`       // arrays flatten
 */

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/** Mark a string as already-safe markup. Rarely needed; prefer nesting `html`. */
export const raw = (value) => ({ __html: String(value) });

const isMarkup = (value) => value !== null && typeof value === 'object' && '__html' in value;

function interpolate(value) {
  if (value === null || value === undefined || value === false || value === true) return '';
  if (Array.isArray(value)) return value.map(interpolate).join('');
  if (isMarkup(value)) return value.__html;
  return escapeHtml(value);
}

export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i += 1) out += interpolate(values[i]) + strings[i + 1];
  return raw(out);
}

/** Unwrap markup for insertion. Plain strings are trusted: they come from our own code. */
export const toHtml = (value) => (isMarkup(value) ? value.__html : String(value ?? ''));

export const qs = (selector, root = document) => root.querySelector(selector);
export const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];

export function render(target, markup) {
  const el = typeof target === 'string' ? qs(target) : target;
  if (el) el.innerHTML = toHtml(markup);
  return el;
}

/**
 * Delegated event binding. Views re-render wholesale, so binding once on a
 * stable container beats re-attaching listeners to every new node.
 */
export function delegate(root, eventName, selector, handler) {
  const el = typeof root === 'string' ? qs(root) : root;
  if (!el) return;
  el.addEventListener(eventName, (event) => {
    const match = event.target.closest?.(selector);
    if (match && el.contains(match)) handler(event, match);
  });
}
