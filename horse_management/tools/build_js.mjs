// Join the scripts every page loads into one file (static/js/app.bundle.js),
// in the order of static/js/bundle.txt. They were each loaded with `defer`,
// which runs them in document order after the page is read; one deferred
// file with the same code in the same order runs the same way, with one
// request instead of one each (pre-launch review).
//
// Run: npm run build:js   (from horse_management/). Commit the result.
// core/tests/test_phone_speed.py checks that the bundle is up to date.
import { readFileSync, writeFileSync } from 'node:fs';

const root = new URL('../static/', import.meta.url);
const list = readFileSync(new URL('js/bundle.txt', root), 'utf8')
    .split('\n').map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));

const parts = list.map((path) => {
    const code = readFileSync(new URL(path, root), 'utf8');
    // A ';' and a new line between files: a file that ends without one
    // must not run into the next.
    return `/* ── ${path} ── */\n${code}\n;\n`;
});
writeFileSync(
    new URL('js/app.bundle.js', root),
    '/* Built by tools/build_js.mjs from static/js/bundle.txt. Do not edit. */\n' + parts.join(''),
);
console.log(`js/app.bundle.js: ${list.length} files`);
