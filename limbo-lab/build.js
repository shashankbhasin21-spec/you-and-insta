#!/usr/bin/env node
/*
 * Build the single-file web app.
 *
 * Replaces each `<!--INLINE:name.js-->` marker in app/index.template.html with the
 * matching file from src/, wrapped in a <script> tag, so the page ships the exact
 * same tested code the CLI and test suite use. Output: app/index.html.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const root = __dirname;
const template = fs.readFileSync(path.join(root, 'app', 'index.template.html'), 'utf8');

const html = template.replace(/<!--INLINE:([\w.-]+)-->/g, (_, name) => {
  const code = fs.readFileSync(path.join(root, 'src', name), 'utf8');
  return `<script>\n/* ${name} — inlined verbatim from src/ by build.js */\n${code}\n</script>`;
});

const outPath = path.join(root, 'app', 'index.html');
fs.writeFileSync(outPath, html);
const kb = (Buffer.byteLength(html) / 1024).toFixed(1);
console.log(`wrote ${path.relative(root, outPath)}  (${kb} KB)`);
if (/<!--INLINE:/.test(html)) { console.error('error: an INLINE marker was left unreplaced'); process.exit(1); }
