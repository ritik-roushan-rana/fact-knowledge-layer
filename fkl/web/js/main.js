/** Boot: mount the App into #root using React 18's createRoot. */

import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App.js';

const rootEl = document.getElementById('root');
createRoot(rootEl).render(createElement(App));
