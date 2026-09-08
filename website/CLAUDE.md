# Project standards
A website for a mixed farm business. Four trading arms: horse livery and foaling,
equine management software, farm contracting with beef and lamb, and camping.
Specification: `docs/plan.md`. Build rules: `docs/handover.md`. Read both.
## Stack
Astro 6 (Node 22.12+), static output. Plain CSS with custom properties.
No UI framework. No Tailwind. Content collections with a Zod schema.
Animation: embla-carousel and gsap only. Nothing else without asking.
## Design bar
The palette is dark green, teal and white. Restrained. Quiet.
Spend boldness in one place: the dial and the banner. Everything else stays disciplined.
Run this before committing any UI. Each line is a known signature of generated
design. If you hit one, change it and say what you changed.
**Colour and surface**
- [ ] Only the tokens in `tokens.css`. No invented greys, no new hues.
- [ ] No gradient wash used as decoration.
- [ ] No tinted near-black (`#0B0B0B`, `#111`) standing in for black. We have `--ink`.
- [ ] No repeated rounded boxes carrying the same soft grey shadow. There is no card kit here.
- [ ] `border-radius` is 0, except 2px on buttons and form fields.
- [ ] No `box-shadow: rgba(0,0,0,.1)` anywhere.
**Typography and chrome**
- [ ] No tracked-out ALL-CAPS label above a heading. Sentence case.
- [ ] No one word of a heading set in a different colour, weight or italic.
- [ ] No meta strings joined with middle dots.
- [ ] No "WORD — fragment" labels built with a spaced em dash.
- [ ] No monospace for small data labels.
- [ ] No arrow appended to link or button text.
- [ ] No 01 / 02 / 03 markers unless the content is genuinely a sequence.
**Motion**
- [ ] No fade-and-slide-up entrance on every section. One page-load sequence, then nothing.
- [ ] No hover lift on cards.
- [ ] Every animation uses the one shared easing curve.
**Structure**
- [ ] Every rule, border, label and divider encodes information. If it only decorates, delete it.
- [ ] One `<h1>` per page.
**Copy**
- [ ] No "seamless", "elevate", "bespoke", "nestled", "passion for", "we pride ourselves",
      "at the heart of", "journey", "unlock", "curated".
- [ ] No three-adjective lists.
- [ ] No em-dash asides.
- [ ] Plain verbs. Sentence case. Say what a thing is, do not sell it.
**The test.** If this page could be dropped onto a dentist's website by swapping the
words, it is too generic. It must be unusable for any other business.
If a browser tool is available, screenshot the page and look at it before committing.
## Built to change weekly
- No content in components. Ever. Not a heading, not a label, not a link.
- Adding a page = one content file, zero code changes.
- Every block survives: a missing optional field, 5 words, 500 words, no image, five images.
- Every schema field carries a comment saying what an editor should put in it.
- Keep `CONTENT.md` current. It is written for someone who does not code.
- Build stays under 60 seconds.
## Never
- Invent a fact. Use `[[TODO: what | owner: who | ref: where]]`, visible on the page.
- Use a stock, external, or generated image. Use `<ImagePlaceholder>`.
- Write privacy, cookie or terms text. Headings and a `[[TODO]]` only.
- Add a feature the plan does not name. List suggestions separately instead.
- Add a dependency without asking.
- Hard-code a hex value outside `tokens.css`.
