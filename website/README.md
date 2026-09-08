# Farm website

The public website for the farm business: livery and foaling, equine
management software, contracting with beef and lamb, and camping.

- `docs/plan.md` is the specification.
- `CLAUDE.md` holds the build standards and the design bar.
- `CONTENT.md` explains how to change the site without touching code.
- `PLACEHOLDERS.md` and `IMAGES-NEEDED.md` list what the site still needs.

## Running it

Node 22.12 or newer.

```
npm ci
npm run dev          # http://localhost:4321
npm run build        # static site in dist/
npm run test         # unit tests for the text helper
npm run placeholders # regenerate the two lists from the content
```

## Where things are

| Path | What |
|---|---|
| `src/content/` | Every word and picture on the site. See CONTENT.md |
| `src/content.config.ts` | The content schema. Every field is commented |
| `src/styles/tokens.css` | Colour, type, spacing, motion. The only file with a hex value |
| `src/styles/base.css` | Fonts, reset, document type, focus, form controls |
| `src/layouts/Base.astro` | Head, header, footer, client router |
| `src/components/nav/` | Header, the dial, the static index, footer |
| `src/components/blocks/` | The ten page blocks and the renderer |
| `src/components/home/` | Banner, shelves, About us, Contact |
| `src/components/ui/` | Picture, placeholder, button, text |
| `src/scripts/` | Dial, banner, page load, About us reveal, gallery, form, header |
| `public/fonts/` | Self-hosted typefaces and their licences |
