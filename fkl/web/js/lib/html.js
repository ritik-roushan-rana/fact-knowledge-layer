/**
 * htm bound to React.createElement — JSX-free templating for React,
 * so we get React's component model without a build step.
 *
 * Usage:
 *   import { html } from './lib/html.js';
 *   const Hello = ({ name }) => html`<div>Hello ${name}</div>`;
 */

import { createElement } from 'react';
import htm from 'htm';

export const html = htm.bind(createElement);
