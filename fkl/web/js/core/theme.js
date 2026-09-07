/**
 * Theme controller: auto (follow the OS), light, or dark.
 *
 * The choice is stored per browser. Storage can throw in private modes, so
 * every access is guarded and the page still renders without it.
 */

const ORDER = ['auto', 'light', 'dark'];
const GLYPHS = { auto: '◐', light: '☀', dark: '☾' };
const STORAGE_KEY = 'fkl-theme';

const read = () => { try { return localStorage.getItem(STORAGE_KEY); } catch { return null; } };
const write = (v) => { try { localStorage.setItem(STORAGE_KEY, v); } catch { /* ignore */ } };

let current = 'auto';

function apply(theme) {
  current = theme;
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
}

export const theme = {
  get current() { return current; },
  init() { apply(ORDER.includes(read()) ? read() : 'auto'); return current; },
  cycle() {
    const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
    apply(next); write(next);
    return next;
  },
  glyph: (t = current) => GLYPHS[t],
};
