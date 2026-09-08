# Farm Website — Plan and Spec

**Draft 1. For review.**
Date: 8 September 2026
Author: prepared for discussion, not for build

---

## 0. Read this first

Four things you need to know before you read the rest.

1. **Your design idea works.** Keep the centre index. Keep the full-screen banner. Two parts need a change. See Section 3.
2. **The developer note does not fit this project.** It was written for a corporate marketing team with an Azure DevOps repository and a blog problem. You are a farm business with four trading arms. See Section 5.
3. **Do not self-host Payload CMS.** It needs a server, a database, backups, updates and a staging site. That is a permanent job for a technical person. You do not have one.
4. **Keep the Astro recommendation.** That part of the note is correct. Pair Astro with a *hosted* CMS. See Section 5.3.

**Decided on 8 September 2026:** Option A. Astro plus a hosted headless CMS. Storyblok is the recommended CMS inside that option. See Section 5.4.

**Animation stack is in Section 11.** Every package is listed, with a verdict and a reason.

**Open questions are in Section 9.** Nine of them block the build. Answer those first.

---

## 1. What the website must do

| Business arm | Who visits | What they must be able to do |
|---|---|---|
| Horse livery and foaling down | Horse owners, mostly local | See the yard, see the facilities, check availability, enquire |
| Equine management software | Yards and owners, anywhere | Understand the product, see it, book a demo, log in |
| Farm — contracting, beef and lamb | Local farmers, local food buyers | See the services and machinery, order meat, get a quote |
| Camping at Colgate Farm | Holiday and weekend visitors | See the site, check dates and prices, book or enquire |
| About us | Everyone | Understand that one family runs all four |

**The problem to solve:** these four audiences do not overlap. A person who wants a campsite does not want yard management software. The home page must send each visitor to the correct place in under five seconds.

Your centre index does exactly that. This is why the idea is good.

---

## 2. Home page structure

### 2.1 Wireframe (desktop)

```
+-----------------------------------------------------------------------+
|  mark                                                     Contact     |
|                                                                       |
|      Livery    Software      ABOUT US      Farm      Camping          |
|      ======                                                           |
|      (active marker travels as the banner rotates)                    |
|                                                                       |
|                                                                       |
|                    [ full-bleed photograph ]                          |
|                                                                       |
|                                                                       |
|                    Foaling down, night and day                        |
|                    One short line of description.                     |
|                                                                       |
|                          [ Read more ]                                |
|                                                                       |
|                            Scroll                                     |
+-----------------------------------------------------------------------+
|                                                                       |
|   SHELF 1 — Livery      short text + one image + link                 |
|   SHELF 2 — Software    short text + one image + link                 |
|   SHELF 3 — Farm        short text + one image + link                 |
|   SHELF 4 — Camping     short text + one image + link                 |
|                                                                       |
|   ABOUT US   the family, the land, the history                        |
|   CONTACT    address, map, phone, form                                |
|   FOOTER     legal, company details                                   |
+-----------------------------------------------------------------------+
```

### 2.2 Section by section

**A. Centre index (your item 1)**
- Five items. Two businesses, About us, two businesses.
- Small type. Wide letter spacing. Sentence case.
- The index stays visible while the banner rotates. This means all four businesses are on screen at all times, even when the photo shows only one.
- The index is also the navigation. It sits over the banner in a translucent bar, and turns solid once the visitor scrolls past the hero.
- **The index behaves as a dial. See Section 12 for the full specification.**

**B. Rotating banner (your item 2)**
- One full-height section. Not four stacked sections.
- Four slides. One per business.
- Each slide holds: photograph, heading, one line of description, one small button.
- Hold time: **6 seconds**. See Section 3.1 for why not 10.
- Transition: slow cross-fade with a small scale on the photograph. 900 ms.

**C. Scroll cue (your item 3)**
- Small word and a mark at the bottom of the banner.
- It fades out after the visitor scrolls 100 pixels.
- **Scroll moves down the page. Scroll does not change the slide.** See Section 3.2.

**D. Shelves (new, recommended)**
- Four short blocks below the banner. One per business.
- Each holds about 40 words and one image.
- Reason: the banner shows one business at a time. The shelves show all four at once. Some visitors will not wait for the rotation.

### 2.3 Mobile

The five-item centre row does not fit on a phone. Use this instead:

```
+---------------------------+
|  mark            [ menu ] |
+---------------------------+
|                           |
|   [ photograph ]          |
|                           |
|   Foaling down,           |
|   night and day           |
|                           |
|   One short line.         |
|                           |
|   [ Read more ]           |
|                           |
|   o o o o   (slide dots)  |
+---------------------------+
```

- Banner height uses `100dvh`, not `100vh`. This prevents the browser bar from cutting the button off.
- The four slide dots replace the centre index. They are tappable.

---

## 3. Problems with the brief, and the fix

These are the two changes I recommend. Both are small. Both matter.

### 3.1 A 10-second hold is too long

**The problem.** A visitor gives a home page about 8 seconds. At 10 seconds per slide, most visitors see one business and leave. Three of your four arms get no view.

Also, an accessibility rule applies. WCAG 2.2 (rule 2.2.2) says that content which moves automatically for more than 5 seconds must have a control to pause it.

**The fix.**
- Hold each slide for **6 seconds**.
- Add a small pause control. A single pause mark near the slide dots is enough.
- Pause the rotation when the pointer is over the banner.
- Pause the rotation when a keyboard user focuses inside the banner.
- Stop the rotation for good after the visitor clicks a dot or an arrow. They have taken control. Do not take it back.
- If the visitor's device requests reduced motion, show slide 1 only and do not rotate. Use the CSS `prefers-reduced-motion` query.

**Result:** the banner still rotates. It no longer traps anybody.

### 3.2 Do not take control of the scroll wheel

**The problem.** "Scroll to find out more" can mean two different things.

- *Meaning A:* the visitor scrolls, and the page moves down to the next section. This is normal. This is correct.
- *Meaning B:* the visitor scrolls, and the page holds still while the banner jumps to the next slide. This is called scroll hijacking. It breaks trackpads, keyboards, screen readers and phone browsers. It also makes people feel they have lost control of the page.

**The fix.** Use meaning A.
- Scroll always moves down the page.
- The banner rotates on a timer only.
- Use CSS `scroll-snap-type: y proximity` on the page. This gives a controlled, deliberate feel. The browser still does the scrolling, so nothing breaks.

**Do not use both.** Auto-rotation plus scroll-driven slides will fight each other and confuse visitors.

### 3.3 Three smaller items

| Item | Risk | Fix |
|---|---|---|
| Page headings | A rotating H1 confuses search engines | One fixed H1 that names the business. Slide headings are H2. |
| Photograph weight | Four full-screen photos load slowly | Load slide 1 first. Load slides 2 to 4 after. Serve AVIF and WebP at several widths. |
| Text over photos | Text becomes unreadable on a bright sky | Put a dark gradient behind the text zone only. Test contrast at 4.5:1. Do not rely on the photo. |

---

## 4. Design direction

This is a proposal, not a decision. It gives you something to react to.

### 4.1 The idea

The four businesses look unrelated. They are not. Foaling down at three in the morning and equine management software are the same instinct: **watch closely, write it down, act early.** That is stockmanship. The site should look like a business run by people who pay attention.

So: quiet, exact, confident. Not rustic. Not corporate.

### 4.2 The one bold move

**The words stay still. The land moves behind them.**

The centre index holds its position. A thin rule travels under the active business as the banner rotates. The photograph changes. Nothing else moves.

One orchestrated moment. Everything else stays disciplined. This is what makes the page memorable, and it ties your item 1 to your item 2 instead of leaving them as two separate features.

### 4.3 Colour

Dark green, teal and white. Restrained. One family of colour, used at different depths.

| Token | Hex | Use | Contrast on white |
|---|---|---|---|
| `--ink` | `#0F2A24` | Body text. Headings. Near-black, but green. | 15.2:1 |
| `--green` | `#14453A` | Dark grounds. Footer. The scrim behind hero text. | 10.8:1 (white text on it) |
| `--teal-deep` | `#1C5C57` | Links. Text that must carry the accent. | 7.7:1 |
| `--teal` | `#2E7F78` | Button fills with white text. The active marker. | 4.75:1 (white text on it) |
| `--teal-light` | `#4A9E93` | Graphics and markers only | 3.2:1 |
| `--stone` | `#5A6B66` | Secondary text. Captions. Meaningful rules. | 5.6:1 |
| `--mist` | `#E4E9E6` | Surface tints. Hairline rules. Placeholder blocks. | Surface only |
| `--paper` | `#FAFAF8` | Page ground | — |
| `--white` | `#FFFFFF` | Cards and panels that must lift off the ground | — |

**Two hard rules.**

1. `--teal-light` never carries body text. It reaches 3.2:1, which passes for large text and for graphics, and fails for anything else.
2. `--mist` is a surface, never a rule that means something. A rule that separates content uses `--stone`.

**Business markers.** Each business takes one step on the same ramp. This differentiates them without introducing a second colour family.

| Business | Marker |
|---|---|
| Livery and foaling | `--green` `#14453A` |
| Farm | `--teal-deep` `#1C5C57` |
| Equine software | `--teal` `#2E7F78` |
| Camping | `--teal-light` `#4A9E93` |

Nothing else changes between businesses. Same type, same layout, same spacing.

### 4.3a The rest of the tokens

A build needs these written down, or every component invents its own numbers.

**Type scale** (1.25 ratio, 18 px base)

| Token | Size | Line height | Use |
|---|---|---|---|
| `--t-hero` | `clamp(2.75rem, 7vw, 5.5rem)` | 0.95 | Banner headings |
| `--t-h1` | `clamp(2.25rem, 5vw, 3.5rem)` | 1.05 | Page titles |
| `--t-h2` | `2rem` | 1.15 | Section headings |
| `--t-h3` | `1.5rem` | 1.25 | Sub headings |
| `--t-body` | `1.125rem` | 1.6 | Body |
| `--t-small` | `0.9375rem` | 1.5 | Captions, footer |
| `--t-nav` | `0.875rem` | 1 | Centre index. Letter spacing `0.08em`. |

**Spacing** (4 px base): `--s-1` 4px, `--s-2` 8px, `--s-3` 16px, `--s-4` 24px, `--s-5` 40px, `--s-6` 64px, `--s-7` 104px, `--s-8` 168px.

**Layout:** max content width `72rem`. Reading column `38rem`. Gutter `--s-4` on mobile, `--s-6` on desktop.

**Radius:** `0` everywhere, except `2px` on buttons and form fields. This site is not made of cards.

**Motion:** `--dur-fast` 200ms, `--dur-mid` 400ms, `--dur-slow` 900ms. One shared ease: `cubic-bezier(0.22, 1, 0.36, 1)`. Every animation on the site uses that curve.

### 4.4 Type

Two families. Clearly different jobs. Both are open licence, so you self-host them and pay nothing.

| Role | Typeface | Why | Install |
|---|---|---|---|
| Display | **Archivo** (variable, with a width axis) | A grotesque that condenses properly. The reference is signwriting on farm lorries and livestock market boards. It has character without shouting. | Fontsource. Confirm the package name. |
| Body | **Source Serif 4** (variable) | Comfortable at 18 px. Designed for screens. Quiet. | Fontsource |

**Alternative body face:** Newsreader, if Source Serif reads too neutral. It has more character and an optical size axis.

**Loading rules:**
- Self-host as `woff2` in `/public/fonts`. Do not link to Google Fonts. It costs a third-party connection and a cookie question.
- `font-display: swap`.
- Preload the display face only. The body face can swap.
- Fallback stack: `Archivo, "Helvetica Neue", Arial, sans-serif` and `"Source Serif 4", Georgia, serif`.

**Do not:**
- Use all-capitals labels above headings.
- Put one word of a heading in a different colour.
- Set body text below 16 px anywhere.

Line length: under 75 characters. The reading column token handles this.

### 4.5 What to avoid

- Rounded cards with soft grey shadows.
- Fade-and-slide-up animation on every section.
- Numbered markers (01 / 02 / 03) unless the content is a real sequence.
- Stock photography. Full-screen heroes fail without real, wide, well-lit photographs of your own land and animals. See Question 7.

---

## 5. Technical setup

### 5.1 Does the developer's note work?

**Partly.** Here is the honest answer.

| Their point | Verdict for this project |
|---|---|
| Use Astro or an equivalent build approach | **Correct. Keep it.** Astro suits a four-section brochure site with heavy photography. |
| Decap has weak Azure DevOps support | **True, but it may not apply.** Decap works normally with GitHub and GitLab. The limitation exists only if your code sits in Azure DevOps. See Question 1. |
| Payload gives Marketing a page builder | **True, and not worth the cost here.** See below. |
| Payload adds hosting, database, auth, backups, staging and support work | **This is the deciding point.** |
| Agree how much Marketing should control before choosing | **Correct process. Apply it.** |

**Why Payload is wrong for you.** Payload is self-hosted. Somebody must run a Node server, run a database, take backups, apply security updates, maintain a staging site and answer the phone when it stops working. A corporate team with developers can absorb that. A farm business cannot. If the person who set it up leaves, the website becomes a liability.

**The context mismatch.** That note mentions Azure DevOps, a Marketing team and a blog publishing problem. None of that appears in your brief. Tell me whether that setup is yours. It changes the answer.

### 5.2 What "anyone can edit" actually needs

You said the setup must be editable and uploadable for everyone or anyone. Break that into four requirements:

1. An editor logs in through a web browser. No software to install.
2. An editor changes text and swaps a photograph without help.
3. An editor cannot break the site layout.
4. Nobody has to run a server.

Payload fails requirement 4. Decap passes 1 to 4 but has a plain editing screen and needs a Git account per editor.

### 5.3 Recommended stack

| Layer | Choice | Note |
|---|---|---|
| Site framework | **Astro** | Static output. Fast. Component based. |
| Styling | Tailwind, or plain CSS with custom properties | Either works. Team preference. |
| Content | **Hosted headless CMS** | See the options table below. |
| Images | CMS image service, or Cloudflare Images | Automatic resizing. Do not hand-crop four sizes. |
| Hosting | Netlify, Cloudflare Pages, or Azure Static Web Apps | Choose Azure only if you already use Azure. |
| Forms | The host's built-in form handling | Enquiries and quotes. |
| Analytics | A cookie-free tool such as Plausible or Fathom | Avoids a cookie banner. |

### 5.4 CMS options, ranked

**Option A — Astro plus a hosted headless CMS. CHOSEN, 8 September 2026.**
- No server. No database to manage. The vendor runs it.
- Two candidates inside this option. **Storyblok is the recommendation.**

| | Storyblok | Sanity |
|---|---|---|
| Editing screen | Visual. The editor clicks the real page and edits in place. | A form-based studio. Developers build the interface. |
| Astro support | Official `@storyblok/astro` package. Handles preview and live editing. | Official package. Live preview must be built. |
| Suits | Marketing pages assembled from approved sections | Deeply structured, cross-referenced content |
| Query language | Standard REST and GraphQL | GROQ. Takes a developer 1 to 2 weeks to learn. |
| Editor risk | Low. It looks like the website. | The editor experience is only as good as the build. |

**Why Storyblok.** Your requirement is "anyone can edit". Storyblok shows the editor the real page while they change it. Sanity gives the developer more power, but a non-technical editor sees an abstract form and has to imagine the result. Your four business owners will each edit their own section. Visual editing removes the guesswork.

Storyblok maps your page blocks (Section 6.4) directly to Astro components. Developers supply the blocks. Editors assemble approved sections. Nobody can break the layout.

**Two costs to check before you sign.** Storyblok bills per environment, so a staging site may count as a second environment. It also caps languages on lower plans. Neither should affect a single-language British site, but confirm both.

**Version note.** Astro 6 requires Node 22.12 or newer. The current integration is `@storyblok/astro` version 9. Confirm both against the docs on the day the build starts.

**Option B — Astro plus Decap CMS. Lowest cost.**
- Free. Content is stored as files in your repository.
- Needs GitHub or GitLab. Do not choose this if the repository is on Azure DevOps.
- The editing screen is plain. It is usable, not pleasant.
- Best fit if one or two confident people edit the site.

**Option C — Squarespace or Webflow. No code.**
- Anybody can edit. Nothing to maintain.
- Webflow can build your rotating banner. Squarespace will struggle with it.
- You give up some of the "very unique" requirement.
- Best fit if nobody in the business will ever touch code again.

**Option D — Self-hosted Payload. Not recommended.**
- Only choose this if you employ or retain a developer with a support agreement.

### 5.5 Cost shape

Do not treat these as quotes. Check current pricing before you decide.

| Item | Order of magnitude |
|---|---|
| Domain | Tens of pounds per year |
| Static hosting | Free to low tens of pounds per month |
| Hosted CMS (Option A) | Free tier, rising to low tens of pounds per month |
| Decap (Option B) | Free |
| Self-hosted Payload (Option D) | Server plus database plus backups plus someone's time, every month |
| Photography | The largest single line item. Budget for it. |

---

## 6. Content model

This is what the CMS must store. Give this to whoever builds it.

### 6.1 Global

- **Site settings:** business name, logo, address, telephone, email, social links, footer text, company registration details.
- **Navigation:** five index items, each with a label and a link.

### 6.2 Home page

- **Hero slides** (4 entries, one per business)
  - Business (link to a Business entry)
  - Photograph (minimum 2400 px wide, landscape)
  - Alt text (required)
  - Heading (maximum 45 characters)
  - Description (maximum 140 characters)
  - Button label
  - Button link
  - Display order

- **Shelves** (4 entries): heading, 40 words of text, one image, link.

### 6.3 Business (4 entries)

Shared fields:
- Name, URL slug, one-line summary
- Marker colour (chosen from the four business markers in Section 4.3)
- Hero photograph
- Introduction text
- Page blocks (see 6.4)

Specific fields:

| Business | Extra fields |
|---|---|
| Livery and foaling | Services list, yard facilities, current availability, foaling season note, prices |
| Equine software | Feature list, screenshots, pricing tiers, demo booking link, customer login link |
| Farm | Contracting services, machinery list, beef and lamb cuts, order method, collection details |
| Camping | Pitch types, season dates, prices, site rules, what to bring, booking link |

### 6.4 Page blocks (shared library)

The editor builds a page by stacking these. Developers supply them. Editors cannot break the layout.

1. Rich text
2. Image and text (image left or right)
3. Photograph gallery
4. Facts list (label and value pairs)
5. Price table
6. Questions and answers
7. Enquiry form
8. Call to action
9. Quotation or testimonial
10. Map and directions

### 6.5 Other pages

- About us
- Contact
- News or updates (optional. Only build this if somebody will write it.)
- Privacy notice, cookie notice, terms

---

## 7. Non-functional requirements

**Performance** (measured on a mid-range Android phone on a 4G connection)
- Largest Contentful Paint: under 2.5 seconds
- Cumulative Layout Shift: under 0.1
- Interaction to Next Paint: under 200 milliseconds

**Accessibility**
- Target WCAG 2.2 level AA.
- Every function works with a keyboard alone.
- Focus outlines stay visible.
- The banner has a pause control.
- The site honours `prefers-reduced-motion`.
- Body text meets 4.5:1 contrast, including over photographs.

**Search**
- One H1 per page.
- Each business has its own page and its own page title.
- Add LocalBusiness structured data. Add Campground data for the campsite.
- Register the business on Google Business Profile. Camping and livery are local searches.

**Legal (United Kingdom)**
- Privacy notice.
- Cookie banner only if you use cookies that are not strictly necessary. A cookie-free analytics tool avoids this.
- Company details in the footer.
- If you sell meat online, add distance selling information and check your food business registration.
- Check whether the campsite licence requires anything on the website.

---

## 8. Phases

| Phase | Work | Rough time |
|---|---|---|
| 0 | Answer the questions in Section 9. Confirm repository, editors and budget. | 1 week |
| 1 | Write copy for four businesses. Take photographs. Runs alongside Phase 2. | 2 to 3 weeks |
| 2 | Design: tokens, home page, one business page template. Sign off. | 2 weeks |
| 3 | Build: Astro, components, home page and four business pages. | 2 to 3 weeks |
| 4 | Connect the CMS. Train the editors. Write a one-page edit guide. | 1 week |
| 5 | Performance, accessibility, search, forms, analytics. Launch. | 1 week |

**Phase 1 is the risk.** Copy and photographs are usually late. Start them on day one.

**Season note:** camping bookings and foaling season both start early in the year. Work back from whichever season you need to catch.

---

## 9. Questions I need answered

The first three block everything else.

**1. Where does your code live?**
Azure DevOps, GitHub, GitLab, or nowhere yet?
*Why it matters:* it decides whether Decap is available to you.

**2. Who will edit the site?**
How many people? How confident are they with computers? Does each business area have its own person?
*Why it matters:* it decides between Options A, B and C.

**3. What is the budget?**
Two numbers: the one-off build, and the monthly running cost you will accept.
*Why it matters:* it decides how much custom work is possible.

**4. One website, or more than one?**
The equine software sells to yards anywhere. The campsite sells to visitors nearby. Should the software have its own domain later?

**5. Camping bookings: real or enquiry?**
Do you need live availability and card payment, or an enquiry form and a telephone call?

**6. Beef and lamb: online orders?**
Card payment and delivery, or an order form and collection?

**7. Do you have photographs?**
Wide, high-resolution, well-lit images of all four businesses. Be honest here. A full-screen banner with weak photographs looks worse than no banner at all.

**8. What is the parent business called?**
Is "Colgate Farm" the whole business, or only the campsite?

**9. Do you already own a domain?**
And is anything hosted there now?

**10. Is there a deadline?**

---

## 10. What I changed from your brief, and why

| Your idea | Change | Reason |
|---|---|---|
| 5 to 10 second slide hold | 6 seconds, with a pause control | Accessibility rule 2.2.2. Also, visitors leave before slide 3. |
| Scroll to find out more | Scroll moves down the page, not between slides | Scroll hijacking breaks trackpads, keyboards and phones. |
| Banner only | Banner plus four short shelves below it | Shows all four businesses at once for visitors who do not wait. |
| Fading / rotating / pivoting | Cross-fade with a small scale, 900 ms | Pivot and slide read as cheap. Cross-fade reads as classy. |
| Centre index as a heading row | Centre index also acts as the live slide indicator | Ties your two features together. This becomes the memorable part. |

Everything else in your brief is unchanged.

---

# 11. Animation stack

Added 8 September 2026. This section covers every animation package considered, a verdict on each, and a map of what moves in each part of the home page.

---

## 11.1 The rule that comes before any package

Read this before the tables.

**Spend your boldness in one place.** The site has one signature move: the words hold still and the land moves behind them. Everything else stays quiet.

The most common failure on a site like this is not a missing library. It is too much motion. A fade-and-slide-up entrance on every section, a hover lift on every card, and a counter on every number all read as templated. Visitors have seen it thousands of times.

So the test for every package below is the same:

> Does this animation tell the visitor something, or does it only decorate?

If it only decorates, cut it.

**Three hard rules for the build:**

1. Animate `transform`, `opacity` and `filter` only. Never animate `top`, `left`, `width`, `height` or `margin`. Those force the browser to recalculate layout on every frame.
2. No animation runs before the largest image has painted.
3. Every animation has a defined end state that appears instantly when the visitor asks for reduced motion.

---

## 11.2 Four layers

Work down this list. Only move to the next layer when the one above cannot do the job.

| Layer | What it is | Cost | Use it for |
|---|---|---|---|
| 1 | The browser itself. CSS and Web APIs. | 0 kB | Most of this site |
| 2 | Small single-purpose packages | 3 to 10 kB each | The banner, smooth scroll |
| 3 | GSAP | About 30 kB plus plugins | Sequenced, orchestrated motion |
| 4 | Specialist engines. Lottie, WebGL, physics. | 30 to 150 kB | Nothing on this site |

**Most of your brief sits in Layer 1.** That is the useful finding. The browser now does scroll-linked animation natively, off the main thread. Three years ago this needed a library.

---

## 11.3 The register

### Layer 1 — Browser native. No package.

| Feature | What it does | Support today | Verdict |
|---|---|---|---|
| **CSS scroll-driven animations** (`animation-timeline: scroll()` and `view()`) | Ties a keyframe animation to scroll position or to an element entering view. Runs on the compositor, so it stays smooth even when the main thread is busy. | Chrome, Edge and Opera 115 and newer. Safari 26. Firefox behind a flag. About 80 percent of visitors. It is a 2026 Interop focus area, so Firefox should follow. | **Use.** With a fallback. See the warning below. |
| **CSS `scroll-snap`** | Holds each section at a controlled stop point without taking over the scroll wheel. | All browsers | **Use** on the page sections |
| **View Transitions API** | Animates between two page states, including between two documents. | Chromium fully. Astro supplies a fallback for the rest. | **Use** through Astro |
| **`@starting-style` and `transition-behavior: allow-discrete`** | Lets an element animate in from `display: none`. Removes most of the reason to reach for a library on menus and dialogs. | Wide | **Use** |
| **`IntersectionObserver`** | Tells you when an element enters the viewport. | All browsers | **Use** as the fallback path |
| **Web Animations API** | The engine underneath modern CSS animation. Motion is built on it. | All browsers | Use indirectly |
| **`prefers-reduced-motion`** | Reports that the visitor has asked their device to reduce motion. | All browsers | **Mandatory** |

**Warning on scroll-driven animations.** A browser that does not understand `animation-timeline` does not ignore the whole rule. It drops that one line and keeps the rest. So `animation: fade-up 1s both` still runs, but as a normal time-based animation. It fires immediately on page load, all at once, in the wrong place. Your calm reveal becomes a flash.

**The fix.** Always wrap the animation in `@supports (animation-timeline: scroll())`, and set the visible end state as the default outside that block. Never rely on a polyfill for a decorative effect. Polyfills for this run tens of kilobytes and load in browsers that do not need them.

---

### Layer 2 — Small packages

| Package | Version checked | Size (gzipped, approximate) | Licence | What it gives you | Verdict |
|---|---|---|---|---|---|
| **Embla Carousel** | 8.6 stable, 9.0 release candidate | 4 to 7 kB core. Plugins are separate packages. | MIT | Slide engine. Official plugins for Autoplay, Fade, Class Names, Auto Height, Auto Scroll and Accessibility. Framework-free, and no hydration mismatch on a static build. | **Use.** This drives the hero banner. |
| **Lenis** | 1.3.x | About 5 kB | MIT | Momentum scrolling that wraps the browser's own scroll. `position: sticky`, anchor links and keyboard scrolling keep working. Syncs to the GSAP ticker in four lines. Has a snap extension. | **Optional.** See 11.4, row F. |
| **Motion** (was Framer Motion and Motion One) | 13.x | 2.3 kB for `useAnimate` mini. 17 kB hybrid. 34 kB for the full React component, or 4.6 kB with `LazyMotion`. Vanilla `animate()` is small. | MIT | Spring physics and a clean API, built on the Web Animations API. Vanilla, React and Vue entry points. | **Hold.** Only if you add React islands. Do not ship it alongside GSAP. |
| **Splide** | current | About 27 kB | MIT | A carousel that is accessible by default. It writes ARIA labels and live-region announcements without configuration. | **Fallback pick.** Choose this instead of Embla if nobody on the build will hand-write ARIA. |
| **Swiper** | 11.x | About 47 kB full. About 20 kB with only Navigation, Pagination and Autoplay. | MIT | Seven built-in transition effects, grid mode, rewind mode. The richest option. | **Reject.** 47 kB to cross-fade four photographs. |
| **CountUp.js** | current | About 3 kB | MIT | Counts a number up when it enters view. | **Hold.** Only if you have a number worth counting. Use it once at most. |
| **Splitting.js** | current | About 4 kB | MIT | Splits text into lines, words and characters so each can animate. | **Reject.** GSAP SplitText is now free and does this better. |

**Embla version note.** The official Accessibility plugin (`embla-carousel-accessibility`) only exists on version 9, which is still a release candidate. On stable version 8 you write the ARIA yourself. Decide this in the proof of concept. If nobody wants to hand-write ARIA, use Splide instead.

---

### Layer 3 — GSAP

**The licence changed.** Webflow acquired GreenSock in October 2024. Since 30 April 2025 the whole GSAP toolset is free, including every plugin that used to sit behind a paid Club membership, and the standard licence now covers commercial use.

This matters. SplitText, MorphSVG, DrawSVG and ScrollSmoother used to cost money. They no longer do. Check the current licence terms before launch, because the licence does list some prohibited uses.

| Plugin | What it does | Verdict for this site |
|---|---|---|
| **gsap** (core) | Timelines, easing, tweening | **Use.** One page-load sequence. |
| **ScrollTrigger** | Starts and scrubs animation from scroll position | **Use sparingly.** CSS scroll-driven animation covers most cases at 0 kB. Use ScrollTrigger only where you need a sequence, not a single property. |
| **SplitText** | Splits a heading into lines, words or characters. Rewritten in 2025: half the previous file size, screen-reader accessibility built in, and masking for reveals. | **Use once.** On the About us paragraph. Split by line, not by character. Character-by-character reveals on a farm website read as showing off. |
| **Flip** | Measures an element in position A, then animates it to position B | **Use.** The dial reorders the DOM on every turn, so focus order keeps matching visual order. Flip is what makes a DOM reorder look like a smooth rotation. See Section 12.7. |
| **Observer** | Normalises wheel, touch and pointer input | **Reject.** This is the tool you would use to hijack the scroll. Section 3.2 says do not. |
| **ScrollSmoother** | GreenSock's smooth-scroll layer | **Reject.** It has known conflicts with Astro's client router. Use Lenis if you want smooth scroll. |
| **DrawSVG** | Draws an SVG line as if by hand | **Hold.** Useful if the identity mark is a line drawing. |
| **MorphSVG** | Morphs one shape into another | **Reject.** No use case here. |
| **Draggable** and **InertiaPlugin** | Drag and throw | **Reject.** Embla handles swipe. |
| **CustomEase** | Draw your own easing curve | **Use.** One shared ease for the whole site. This is what makes motion feel like one hand made it. |
| **`gsap.matchMedia()`** | Runs animations only when a media query matches, and cleans up when it stops matching | **Mandatory** for the reduced-motion contract |

---

### Layer 4 — Specialist. All rejected.

| Package | What it is | Why not |
|---|---|---|
| **Lottie** (`lottie-web`, `@lottiefiles/dotlottie-web`) | Plays After Effects animations as vector | Only worth it if you commission an animated mark. That is a design decision, not a technical one. Revisit if the brand work produces one. |
| **Rive** | Interactive vector animation with a state machine | Same as Lottie, with a runtime cost. No use case. |
| **Three.js**, **React Three Fiber**, **OGL** | WebGL 3D | 150 kB and a real risk on mid-range phones. A farm website does not need a 3D scene. |
| **Curtains.js** and WebGL image transitions | Shader-based photo transitions | The effect is impressive and it destroys your performance budget. A 900 ms cross-fade reads more expensive anyway. |
| **Matter.js** | 2D physics | No use case. |
| **tsParticles** and **Vanta.js** | Animated backgrounds | Decoration with no meaning. Also a battery cost. |
| **Theatre.js** | A visual timeline editor for web animation | The site has one sequence. It does not need a timeline editor. |
| **Barba.js** | Page transitions | Superseded by the View Transitions API and Astro's router. |
| **ScrollMagic** | Scroll-linked animation | Effectively dead. ScrollTrigger replaced it. |
| **anime.js** | General animation library | A good library. It overlaps GSAP entirely. Do not ship two engines. |
| **typed.js** | Typewriter effect | Reject on taste. |

### Also rejected, and this one matters

| Package | Why not |
|---|---|
| **AOS (Animate On Scroll)** and **ScrollReveal** | These two are the reason so many sites look the same. They apply one fade-and-slide-up to every section on the page. It is the single clearest sign that nobody made a design decision. CSS `animation-timeline: view()` does the same job at 0 kB, and forces you to choose what moves. |

---

## 11.4 What moves, section by section

| | Element | What happens | Driver | Package | Reduced motion |
|---|---|---|---|---|---|
| A | **Page load** | One sequence. Index items set first, then the hero photograph rises from a slight blur and scale. About 700 ms total, then it stops. | GSAP timeline | `gsap` core, `CustomEase` | Everything appears at once |
| B | **Centre index, hover and focus** | A hairline under the item draws out from the centre. 200 ms. | CSS `transform: scaleX()` transition | none | No transition. The rule appears. |
| C | **Hero cross-fade** | Photograph A fades to photograph B over 900 ms. The active photograph scales slowly from 1.00 to 1.04 across its 6-second hold. | Embla, Fade plugin, Autoplay plugin, Class Names plugin | `embla-carousel` and 3 plugins | Autoplay never starts. Slide 1 only. Dots still work. |
| D | **The dial** | The index turns one or two detents. Four items translate, one wraps round to the other side. Full spec in Section 12. | GSAP Flip over a DOM reorder | `gsap`, `Flip` | Order changes instantly. Nothing hidden. |
| E | **Scroll cue** | A small mark breathes slowly. It fades out over the first 100 px of scroll. | `animation-timeline: scroll()` for the fade. CSS keyframes for the breathing. | none | Static. Visible. No breathing. |
| F | **Sticky header** | The header shrinks and gains a bottom rule as the visitor leaves the hero. | `animation-timeline: scroll()`. This is the textbook case for it. | none | Fallback: `IntersectionObserver` on a sentinel element toggling a class. |
| G | **Four shelves** | The shelf image scales from 1.06 to 1.00 as the shelf crosses the viewport. Nothing else moves. | `animation-timeline: view()` with `animation-range` | none | Static at 1.00 |
| H | **About us paragraph** | Lines rise into view behind a mask, one after another. Used once on the whole site. | GSAP SplitText, split by line, with masking | `gsap`, `SplitText` | Text visible. No reveal. |
| I | **Enquiry form** | Error and success states appear with a short transition. | CSS `@starting-style` plus `transition-behavior: allow-discrete` | none | Instant state change |
| J | **Page to page** | The hero photograph carries from the home page into the business page. This single transition does more for the feel of the site than everything above it. | Astro `<ClientRouter />` with `transition:name` on the photograph | `astro:transitions` (built in) | Astro disables it automatically |
| K | **Photograph galleries** | Swipe and arrow navigation. Lightbox on click. | Embla again, already loaded. Lightbox with the native `<dialog>` element plus a view transition. | `embla-carousel` | Navigation works. No animation. |
| L | **Numbers**, if any | Count up on entry. | GSAP or CountUp.js | `countup.js` | Final number shown |

**Rows B, D, E, F, G and I need no package at all.** Six of the twelve. That is the point of Layer 1.

---

## 11.5 Astro build notes

These are the failures that will cost your developer a day each. They are all documented and all avoidable.

**1. Scripts do not re-run after client-side navigation.**
Astro's `<ClientRouter />` swaps the page body instead of reloading the document. Your GSAP and Embla setup code runs once, then never again. The visitor goes to the camping page, comes back, and the banner is dead.

Hook the Astro lifecycle events instead of `DOMContentLoaded`:
- `astro:page-load` runs after the first load **and** after every client-side navigation. Put initialisation here.
- `astro:before-swap` runs before the new page replaces the old. Put teardown here.

**2. Tear down before you rebuild.**
Without teardown, ScrollTrigger instances stack up on every navigation. The page slows down and leaks memory.

```js
document.addEventListener('astro:before-swap', () => {
  ScrollTrigger.getAll().forEach((t) => t.kill());
  emblaApi?.destroy();
});
```

**3. GSAP ScrollSmoother and Astro's router do not work together.**
This is a reported and repeated problem. ScrollSmoother stops working after the first navigation. If you want momentum scrolling, use Lenis, which wraps native scroll and survives the swap.

**4. Keep the hero script off the critical path.**
The banner script must not delay the largest image. Load the photograph first. Start the carousel after. If the banner lives in a framework island, use `client:visible` or `client:idle`, never `client:load`.

**5. Version floor.**
Astro 6 requires Node 22.12 or newer. Node 18 and 20 no longer work.

---

## 11.6 The reduced-motion contract

One rule, written down so nobody has to guess:

> Reduced motion means the visitor sees the **end state, immediately**. It never means content is hidden, and it never means a feature stops working.

| Layer | How it honours the setting |
|---|---|
| CSS | Wrap every `@keyframes` use in `@media (prefers-reduced-motion: no-preference)`. Set the visible end state as the default. |
| GSAP | `gsap.matchMedia()` with `"(prefers-reduced-motion: no-preference)"`. It cleans up automatically when the setting changes. |
| Embla Autoplay | Check the media query before you start it. Never start it. Embla's docs recommend a media query in the `breakpoints` option to toggle plugins. |
| Astro ClientRouter | Automatic. Astro ships a media query that disables all view transitions, including the fallback animation. |
| Motion, if used | `useReducedMotion` in React. A `matchMedia` check in vanilla. |

**Test it.** On macOS: System Settings, Accessibility, Display, Reduce motion. On Windows: Settings, Accessibility, Visual effects, Animation effects. On iOS and Android: Accessibility, Motion.

---

## 11.7 Performance budget

Animation is where a beautiful site becomes a slow site. Fix the numbers before you build.

| Measure | Limit |
|---|---|
| Total JavaScript on the home page, animation included | 60 kB gzipped |
| Largest Contentful Paint, mid-range Android on 4G | Under 2.5 seconds |
| Cumulative Layout Shift | Under 0.1 |
| Frame rate during the cross-fade | 60 frames per second on a 4-year-old phone |

**How to stay inside it:**
- Layer 1 costs 0 kB. Use it first.
- Embla core plus three plugins comes to roughly 10 kB. That is the main animation cost.
- GSAP core plus SplitText is roughly 40 kB. Load it **only on the pages that use it**, not sitewide.
- Do not ship GSAP and Motion together. Pick one engine.
- Set `will-change` only on the element currently moving, and remove it afterwards. A permanent `will-change` holds a compositor layer open and costs memory.
- Test on a real mid-range Android handset. Not on a laptop with the throttle turned on.

**Check the sizes yourself before you commit.** The figures above are approximate and change with every release.

---

## 11.8 The install list

**Confirmed:**
```
npm i embla-carousel embla-carousel-fade embla-carousel-autoplay embla-carousel-class-names
npm i gsap
```

**Add only if the proof of concept shows a need:**
```
npm i embla-carousel-accessibility   # version 9 only, still a release candidate
npm i lenis                          # only if momentum scroll is wanted
npm i countup.js                     # only if there is a number worth counting
```

**Built in, nothing to install:**
- `astro:transitions` for page-to-page motion
- CSS scroll-driven animations
- CSS `scroll-snap`
- `IntersectionObserver`

**Do not install:** AOS, ScrollReveal, Swiper, anime.js, Motion (unless React islands arrive), ScrollMagic, Barba, Three.js, Lottie, tsParticles.

---

## 11.9 Prove it before you build it

Build one throwaway page. Half a day. It answers every open technical question at once.

1. Astro 6, static output, on Node 22.12 or newer.
2. Four photographs in an Embla carousel. Fade plugin, Autoplay at 6 seconds, Class Names plugin.
3. The centre index above it, with the marker travelling on Embla's `select` event.
4. A sticky header that shrinks, driven by `animation-timeline: scroll()`.
5. `<ClientRouter />` and a second page, with `transition:name` on the photograph.
6. Navigate away and back. **Confirm the banner still runs.** This is the test that matters.
7. Turn on reduced motion. Confirm nothing moves and nothing disappears.
8. Open it on a real mid-range Android phone. Measure LCP.

If steps 6, 7 and 8 pass, the stack is proven. Then start the real build.

---

## 11.10 Sources checked

- Webflow, "Webflow makes GSAP 100% free" and GSAP release notes 3.13. Licence change effective 30 April 2025.
- Chrome for Developers and MDN, scroll-driven animations. Support: Chrome, Edge and Opera 115 and newer, Safari 26, Firefox behind a flag.
- Astro documentation, view transitions and the `<ClientRouter />` component, including automatic `prefers-reduced-motion` support and the need to reinitialise scripts.
- GSAP support forums, reported ScrollTrigger and ScrollSmoother failures with Astro view transitions.
- Embla Carousel documentation, plugin list and the version 9 Accessibility plugin.
- Motion documentation, bundle sizes.
- Lenis repository and npm, version 1.3.x.
- Storyblok and Sanity comparisons, and the `@storyblok/astro` integration guide.

**Verify every version number and file size on the day the build starts.** All of it moves.

---

# 12. The dial

Added 8 September 2026. This replaces the static centre index. Read Section 2.2 first.

---

## 12.1 What it is

The five index items sit on a ring, not a list. The centre slot is the front position and it always names where the visitor is. Turning the dial is how you move around the site.

**About us holds the centre on the landing page.** The four businesses sit around it, two on each side.

```
Rest. The visitor is on the landing page.

    Livery     Software     ABOUT US      Farm     Camping
      -2          -1            0          +1         +2
```

Select Farm. The dial turns one detent to the left. Four items slide one slot. The item that runs off the left end appears at the right end.

```
    Software   About us       FARM      Camping    Livery
      -2          -1            0          +1         +2
                                                       ^
                                          Livery has wrapped from -2 round to +2
```

Select Camping from rest. The dial turns two detents. Two items wrap.

**The dial never hides anything.** Five items, five slots, always. That was the whole reason for the centre index, and the dial keeps it.

---

## 12.2 Two states

This is the part that needs a decision, because it decides how the dial behaves while the banner is rotating.

| State | When | What the dial does |
|---|---|---|
| **Rest** | The visitor lands and has not touched anything. About us holds the centre. | The dial does **not** turn. The banner rotates underneath it. The business currently showing brightens and takes the 2 px marker. |
| **Selected** | The visitor turns the dial, clicks a slide dot, or scrolls | The chosen business comes to the centre. The banner and the focus section lock to it. Rotation stops for good. |
| **Home** | The visitor turns the dial back to About us | Returns to rest. Rotation does not restart. The visitor has taken control. |

**Why the dial must not turn on the timer.** If the banner spins the dial every six seconds, the navigation is in constant motion. That is tiring, it makes the dial hard to aim at, and it breaks the reading that the centre slot means "you are here". A slow, deliberate turn that only happens when a person asks for it is what makes it feel like quality rather than a gimmick.

So while the banner is rotating, the dial holds still and only the marker moves. The moment a person acts, the dial takes over and the banner stops.

**Assumption to confirm:** the centre slot names the current page, and About us at centre means the visitor is on the landing page. Say if you meant something else.

---

## 12.3 Slot treatment

The depth cues are what make it read as a dial rather than a sliding list. Keep them small. The whole effect should be felt before it is noticed.

| Slot | Opacity | Scale | Horizontal squeeze | Vertical offset | Weight |
|---|---|---|---|---|---|
| `0` centre | 100% | 1.00 | none | 0 | 600 |
| `±1` | 80% | 0.96 | `scaleX(.98)` | +1 px | 500 |
| `±2` | 52% | 0.90 | `scaleX(.94)` | +3 px | 500 |

**The horizontal squeeze is the trick.** Compressing the outer items by a few percent suggests a surface curving away from the viewer. It costs one CSS property and it does more work than any other cue on this list. Without it the dial reads as a slider.

**Edge mask.** Fade both ends of the bar so items dissolve into the edge instead of being clipped by it:

```css
mask-image: linear-gradient(to right, transparent 0, black 11%, black 89%, transparent 100%);
```

The mask also hides the seam. See 12.5.

---

## 12.4 Motion

| Property | Value |
|---|---|
| One detent | 520 ms |
| Two detents | 700 ms |
| Maximum turn | 2 detents. Always take the shortest way round the ring. |
| Easing | The shared site curve, `cubic-bezier(.22, 1, .36, 1)` |
| Settle | Optional. A 2 px counter-move over the last 90 ms. |

**On the settle.** A real dial stops against a detent. A 2 px counter-move suggests that. Build it, look at it, and cut it if it reads as a bounce. A bounce is the difference between a mechanism and a toy.

**Never spin through.** A four-step spin to reach the far side looks like a slot machine. The ring has five positions, so nothing is ever more than two steps away. Cap it at two and take the short route.

**One turn at a time.** Ignore new input while a turn is running, or queue it. Two overlapping turns look broken.

---

## 12.5 The seam

When an item leaves one end and appears at the other, it must not slide across the middle. That is the whole problem, and there are three ways to solve it.

| Approach | How | Verdict |
|---|---|---|
| **Fade at the seam** | The wrapping item fades to 0 over the first 40% of the turn, jumps position with transitions off, then fades in over the last 40%. | **Recommended.** Five DOM nodes, no duplicates. The edge mask hides most of it already. |
| **Ghost copies** | Render the ring three times. Translate a long strip, then silently reset when nothing is moving. | Works, and never shows a seam. But it puts the navigation into the accessibility tree three times. Extra work to hide the copies correctly. |
| **True arc** | Place items on the circumference of a large circle with perspective. Wrapping happens behind the vanishing point, so there is no seam at all. | The most convincing. The most expensive. Hard to keep accessible. Only if the first two look wrong. |

Start with the fade. Test it against a bright photograph, which is where a seam shows worst.

---

## 12.6 Accessibility

This is where dials normally fail. Four requirements, all non-negotiable.

**1. It is still a list of links.** A `<nav>` containing a `<ul>` of five `<a>` elements. The dial is presentation. Nothing about the markup changes.

**2. Focus order must follow visual order.** If the DOM order stays fixed while the visual order rotates, keyboard focus jumps around the bar unpredictably. That breaks WCAG 2.4.3.

**The fix, and it is the reason Flip moved to "Use" in Section 11:** reorder the actual DOM on every turn, then use GSAP Flip to animate from the old positions to the new ones. Flip measures where everything was, lets you reorder, then animates the difference. You get a correct DOM and a smooth turn from one mechanism.

**3. Keyboard.**

| Key | Action |
|---|---|
| Tab | Moves through the five items in visual order |
| Left and Right arrow | Turns the dial one detent and carries focus with it |
| Home | Returns About us to the centre |
| Enter | Follows the link |

If a focused item wraps to the other side, focus stays on that item. Never drop focus mid-turn.

**4. Announce it.** A polite live region says the new centre: "Farm. Selected." Do not announce every slot.

**Reduced motion.** No turn. The order changes instantly. Nothing fades, nothing is hidden, every link still works.

---

## 12.7 Across pages

The dial gives you something better than a nice hover effect.

Put `transition:persist` on the header so Astro's client router carries it across the document swap instead of destroying and rebuilding it. The dial then turns **during** the navigation. The visitor clicks Farm, the dial rotates Farm to the centre, and the page underneath changes in the same motion.

That is one continuous action rather than a click followed by a page load. It is the cheapest way to make the site feel expensive, and it costs one directive.

Two build notes:
- The dial state comes from the route, not from a variable. Landing on `/farm` directly must show Farm at the centre with no turn animation.
- Set the initial slots in CSS from a server-rendered attribute. Never set them in JavaScript on first paint, or the dial will visibly snap into place after load.

---

## 12.8 Mobile

Five slots with three size tiers does not fit a 320 px screen. Do not try.

| Width | Slots shown |
|---|---|
| Above 880 px | 5. Centre and ±2. |
| 620 to 880 px | 5. Tighter gaps, smaller type. |
| Below 620 px | **3.** Centre and ±1. The ±2 items sit under the edge mask, half visible, so the visitor can see the ring continues. |

Three visible slots with two bleeding off the edges is how a physical picker wheel behaves. It is more natural on a phone than five cramped items, not less.

Add swipe on the bar below 620 px. One swipe turns one detent.

---

## 12.9 Build notes

**Component:** `src/components/nav/Dial.astro` plus `src/scripts/dial.ts`.

**Data:** the five items come from site settings, in ring order. The order is content, not code.

**State:** one number, the index of the centre item, 0 to 4. Everything else derives from it.

**Positioning:** each item carries `data-slot` from `-2` to `2`. CSS reads it. JavaScript changes it. No inline styles for position.

**Performance:** animate `transform` and `opacity` only. The dial sits above the fold, so it must not delay the largest image. Slots are set server side. The script loads on idle.

**Do not:** use a carousel library for this. It is five items on a fixed ring. Embla is for the banner and would fight the DOM reorder.

---

## 12.10 The risk, and the fallback

A dial is not standard navigation. Some visitors will not recognise it as a menu, and they will be the ones least likely to say so.

**Keep the static index in the codebase.** It works, it is already built, and it is one flag away. If testing shows people miss it, switch back and lose nothing but the turn.

**Two things that reduce the risk:**
- All five labels are readable at all times. Nobody has to turn the dial to find out what is on it.
- The centre slot is visibly different from the others. That is what tells a visitor the middle position means something.

**Test with three people who have never seen the site.** Ask them to find the campsite. If they use the dial without being told, it works. If they scroll past it looking for a menu, it does not.
