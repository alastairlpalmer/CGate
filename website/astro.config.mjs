// @ts-check
import { defineConfig } from 'astro/config';

// Static output. No adapter. The site is plain HTML, CSS and a little JavaScript.
// The hosting choice is still open (docs/plan.md, Section 5.3), so `site` is a
// placeholder until the domain is confirmed.
export default defineConfig({
  output: 'static',
  site: 'https://colgatefarm.co.uk',
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  devToolbar: { enabled: false },
});
