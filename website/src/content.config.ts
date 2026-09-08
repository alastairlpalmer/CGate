/*
  Content schema. Every field carries a comment saying what an editor puts in it.

  The shapes are flat and named so they lift straight into Storyblok later:
  each block's `type` is the future Storyblok component name, and each
  collection is a future content type. See docs/plan.md, Section 6.

  A fact nobody has supplied yet is written in the text as
  [[TODO: what is needed | owner: who supplies it | ref: where it belongs]]
  and shows on the page until somebody replaces it.
*/
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

/* ---------- Shared pieces ---------- */

// The four trading arms. The value sets the marker colour and ties a slide,
// a shelf or a page to its business.
export const markerSchema = z.enum(['livery', 'software', 'farm', 'camping']);

// A photograph. Leave `src` out until the real photograph exists; the page
// then shows a placeholder block carrying the `brief`.
export const imageSchema = z.object({
  // Path of the photograph under /public, for example "/images/yard-march.jpg".
  // Leave out until the photograph exists.
  src: z.string().optional(),
  // What is in the photograph, for people who cannot see it. Always required.
  alt: z.string(),
  // What photograph is needed. Shown on the page until `src` exists, and
  // collected into IMAGES-NEEDED.md.
  brief: z.string().optional(),
  // Who is taking or supplying the photograph.
  owner: z.string().optional(),
  // The shape of the space the photograph fills. Default is landscape (3:2).
  aspect: z.enum(['landscape', 'portrait', 'square', 'wide']).default('landscape'),
  // Pixel width and height of the file, when known. Stops the page jumping while it loads.
  width: z.number().int().positive().optional(),
  height: z.number().int().positive().optional(),
});

// A link. `href` is a path on this site ("/camping/") or a full address
// ("https://...", "tel:", "mailto:").
export const linkSchema = z.object({
  // The words on the link.
  label: z.string(),
  // Where it goes.
  href: z.string(),
});

/*
  Text fields accept plain paragraphs. A blank line starts a new paragraph.
  You may use **bold**, *italic*, [link words](https://address) and lines
  starting with "- " for a list. Nothing else.
*/
const text = z.string();

/* ---------- Page blocks (docs/plan.md, Section 6.4) ---------- */

// 1. Rich text. Heading and paragraphs.
const richTextBlock = z.object({
  type: z.literal('richText'),
  // Optional heading above the text.
  heading: z.string().optional(),
  // The paragraphs.
  text,
});

// 2. Image and text, image left or right.
const imageTextBlock = z.object({
  type: z.literal('imageText'),
  heading: z.string().optional(),
  text,
  image: imageSchema,
  // Which side the image sits on at desktop width. Default right.
  imageSide: z.enum(['left', 'right']).default('right'),
  // Optional link under the text.
  link: linkSchema.optional(),
});

// 3. Photograph gallery. One to many photographs, swipe and arrow navigation.
const galleryBlock = z.object({
  type: z.literal('gallery'),
  heading: z.string().optional(),
  // The photographs, in order.
  images: z.array(imageSchema),
});

// 4. Facts list. Label and value pairs, for things like "Stables: 12".
const factsBlock = z.object({
  type: z.literal('facts'),
  heading: z.string().optional(),
  items: z.array(
    z.object({
      // The name of the fact, for example "Address".
      label: z.string(),
      // The fact itself. Paragraph rules apply.
      value: text,
    }),
  ),
});

// 5. Price table. One row per thing that has a price.
const pricesBlock = z.object({
  type: z.literal('prices'),
  heading: z.string().optional(),
  // A line under the table, for example "Prices include VAT" or a [[TODO]].
  note: z.string().optional(),
  rows: z.array(
    z.object({
      // What is being priced, for example "Full livery, per week".
      label: z.string(),
      // The price as words, for example "£180" or "From £42 a night".
      price: z.string(),
      // Anything else worth saying about that row.
      detail: z.string().optional(),
    }),
  ),
});

// 6. Questions and answers.
const questionsBlock = z.object({
  type: z.literal('questions'),
  heading: z.string().optional(),
  items: z.array(
    z.object({
      question: z.string(),
      // Paragraph rules apply.
      answer: text,
    }),
  ),
});

// 7. Enquiry form. The host's own form handling posts it.
const formBlock = z.object({
  type: z.literal('form'),
  heading: z.string().optional(),
  // A line above the fields, for example who reads the messages and how fast.
  text: text.optional(),
  // Where the form posts. Leave out until the host is chosen; the page shows a [[TODO]].
  action: z.string().optional(),
  // A name for the form, so the host can tell forms apart. Letters and hyphens only.
  name: z.string().default('enquiry'),
  fields: z.array(
    z.object({
      // The field's name in the message you receive. Letters and hyphens only.
      name: z.string(),
      // The words the visitor sees.
      label: z.string(),
      // What sort of field it is.
      kind: z.enum(['text', 'email', 'tel', 'textarea', 'select']).default('text'),
      // Must the visitor fill it in?
      required: z.boolean().default(false),
      // For a select field only: the choices, in order.
      options: z.array(z.string()).optional(),
    }),
  ),
  // The words on the button.
  submitLabel: z.string(),
  // Shown after a successful send.
  successText: z.string(),
  // Shown when a send fails.
  errorText: z.string(),
});

// 8. Call to action. A heading, a line, one button.
const ctaBlock = z.object({
  type: z.literal('cta'),
  heading: z.string(),
  text: text.optional(),
  link: linkSchema,
});

// 9. Quotation or testimonial.
const quoteBlock = z.object({
  type: z.literal('quote'),
  // The words said.
  text: z.string(),
  // Who said them.
  name: z.string().optional(),
  // Anything about them that matters, for example "livery client since 2019".
  detail: z.string().optional(),
});

// 10. Map and directions. No external map embed: an address, directions in
// words, a link to a map service, and a photograph or plan of the approach.
const mapBlock = z.object({
  type: z.literal('map'),
  heading: z.string().optional(),
  // One line per line of the postal address.
  addressLines: z.array(z.string()),
  // How to find the farm, in words. Paragraph rules apply.
  directions: text.optional(),
  // A link to the map service of your choice.
  mapLink: linkSchema.optional(),
  // A photograph of the entrance, or a drawn plan.
  image: imageSchema.optional(),
});

export const blockSchema = z.discriminatedUnion('type', [
  richTextBlock,
  imageTextBlock,
  galleryBlock,
  factsBlock,
  pricesBlock,
  questionsBlock,
  formBlock,
  ctaBlock,
  quoteBlock,
  mapBlock,
]);

/* ---------- Site settings (one file: src/content/settings/site.json) ---------- */

const settings = defineCollection({
  loader: glob({ pattern: '*.json', base: './src/content/settings' }),
  schema: z.object({
    // The name of the whole business, as it appears in the header and the page title.
    name: z.string(),
    // One line under the name in search results and link previews.
    description: z.string(),
    // The logo, if there is one. The header shows the name as words until then.
    mark: imageSchema.optional(),
    nav: z.object({
      // The five index items in ring order, left to right. Two businesses,
      // About us, two businesses. The order is content, not code.
      items: z.array(
        z.object({
          label: z.string(),
          href: z.string(),
          // The business this item belongs to. Leave out for About us.
          marker: markerSchema.optional(),
        }),
      ),
      // true: the index behaves as a dial (docs/plan.md, Section 12).
      // false: the plain static index. One flag, nothing else changes.
      dial: z.boolean().default(true),
      // Name of the navigation for screen readers.
      label: z.string(),
      // What a screen reader hears when the dial settles. {label} is replaced.
      announce: z.string(),
      // Help text for the dial, read by screen readers only.
      help: z.string(),
    }),
    // The header's right-hand link.
    contactLink: linkSchema,
    // Words the interface uses. They are content so they can be changed here.
    ui: z.object({
      // The skip link at the top of every page.
      skipLabel: z.string(),
      // The scroll cue under the banner.
      scrollCue: z.string(),
      // Banner controls.
      bannerLabel: z.string(),
      pauseLabel: z.string(),
      playLabel: z.string(),
      // Dot label. {heading} is replaced with the slide heading.
      slideLabel: z.string(),
      // Gallery controls.
      previousLabel: z.string(),
      nextLabel: z.string(),
      openLabel: z.string(),
      closeLabel: z.string(),
      // Word shown before a placeholder image brief.
      imageNeeded: z.string(),
    }),
    contact: z.object({
      // One line per line of the postal address.
      addressLines: z.array(z.string()),
      phone: z.string().optional(),
      email: z.string().optional(),
    }),
    // Social links, if any. Leave the list empty to show none.
    social: z.array(linkSchema).default([]),
    footer: z.object({
      // Company details required by law: registered name, number, address, VAT.
      // One line each.
      companyLines: z.array(z.string()),
      // Legal pages: privacy, cookies, terms.
      links: z.array(linkSchema).default([]),
      // Anything else, for example the year or a credit.
      text: z.string().optional(),
    }),
    // The page shown for an address that does not exist.
    notFound: z.object({
      title: z.string(),
      text: text,
      link: linkSchema,
    }),
  }),
});

/* ---------- Home page (one file: src/content/home/home.json) ---------- */

const bannerItem = z.object({
  // Which business this belongs to. Sets the marker colour and links the
  // slide to its index item.
  marker: markerSchema,
  // Slide heading. Keep under 45 characters so it fits on a phone in one or two lines.
  heading: z.string(),
  // One line under the heading. Keep under 140 characters.
  text: z.string(),
  // The photograph. Landscape, at least 2400 px wide.
  image: imageSchema,
  // The small button.
  link: linkSchema,
});

const home = defineCollection({
  loader: glob({ pattern: '*.json', base: './src/content/home' }),
  schema: z.object({
    // Browser tab title and search result title.
    title: z.string(),
    // Search result description.
    description: z.string(),
    // The one fixed H1. Names the business. Sits still above the rotating headings.
    h1: z.string(),
    // The banner slides, in rotation order. One per business.
    slides: z.array(bannerItem),
    // The four shelves under the banner. About 40 words each.
    shelves: z.array(
      z.object({
        marker: markerSchema,
        // Used as the anchor in the address bar, for example "livery" gives /#livery.
        id: z.string(),
        heading: z.string(),
        text,
        image: imageSchema,
        link: linkSchema,
      }),
    ),
    about: z.object({
      id: z.string(),
      heading: z.string(),
      // The paragraph that rises into view line by line. One paragraph.
      lead: z.string(),
      // Anything else: stacked page blocks.
      blocks: z.array(blockSchema).default([]),
      // The latest posts from the blog.
      posts: z.object({
        heading: z.string(),
        // How many to show.
        count: z.number().int().positive().default(3),
        // Shown when there are no posts yet.
        emptyText: z.string(),
        // Link to the full list, once the post pages exist.
        link: linkSchema.optional(),
      }),
    }),
    contact: z.object({
      id: z.string(),
      heading: z.string(),
      blocks: z.array(blockSchema).default([]),
    }),
  }),
});

/* ---------- Businesses (four Markdown files: src/content/businesses/*.md) ---------- */

// The file body, below the front matter, is the introduction text.
const businesses = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/businesses' }),
  schema: z.object({
    // Business name as shown on its page.
    name: z.string(),
    // The address of the page, for example "camping" gives /camping/.
    slug: z.string(),
    // One line summary, used in links and search results.
    summary: z.string(),
    // The marker colour. Livery: green. Farm: teal-deep. Software: teal. Camping: teal-light.
    marker: markerSchema,
    // The page photograph. Landscape, at least 2400 px wide.
    hero: imageSchema,
    // The page, built from stacked blocks.
    blocks: z.array(blockSchema).default([]),

    // Fields for one business only. Fill in the one that applies.
    livery: z
      .object({
        // Services offered, one per line.
        services: z.array(z.string()).default([]),
        // Yard facilities, one per line.
        facilities: z.array(z.string()).default([]),
        // Current availability, in words.
        availability: z.string().optional(),
        // A note about the foaling season.
        foalingNote: z.string().optional(),
      })
      .optional(),
    software: z
      .object({
        // Feature list, one per line.
        features: z.array(z.string()).default([]),
        // Screenshots.
        screenshots: z.array(imageSchema).default([]),
        // Where to book a demo.
        demoLink: linkSchema.optional(),
        // Where customers log in.
        loginLink: linkSchema.optional(),
      })
      .optional(),
    farm: z
      .object({
        // Contracting services, one per line.
        services: z.array(z.string()).default([]),
        // Machinery, one per line.
        machinery: z.array(z.string()).default([]),
        // Beef and lamb cuts available, one per line.
        cuts: z.array(z.string()).default([]),
        // How to order, in words.
        orderMethod: z.string().optional(),
        // Where and when to collect, in words.
        collection: z.string().optional(),
      })
      .optional(),
    camping: z
      .object({
        // Pitch types, one per line.
        pitchTypes: z.array(z.string()).default([]),
        // When the site is open, in words.
        season: z.string().optional(),
        // Site rules, one per line.
        rules: z.array(z.string()).default([]),
        // What to bring, one per line.
        bring: z.array(z.string()).default([]),
        // Where to book.
        bookingLink: linkSchema.optional(),
      })
      .optional(),
  }),
});

/* ---------- Other pages (Markdown files: src/content/pages/*.md) ---------- */

// About us, Contact, privacy, cookies, terms. The file body is the main text.
// The file name is the address: "privacy.md" gives /privacy/.
const pages = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/pages' }),
  schema: z.object({
    title: z.string(),
    // One line summary for search results.
    summary: z.string(),
    // Optional page photograph.
    hero: imageSchema.optional(),
    // Anything after the main text: stacked blocks.
    blocks: z.array(blockSchema).default([]),
  }),
});

/* ---------- Blog posts (Markdown files: src/content/posts/*.md) ---------- */

// The file name is the address: "first-foal-2027.md" gives /news/first-foal-2027/.
// The file body is the post.
const posts = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/posts' }),
  schema: z.object({
    title: z.string(),
    // Date written, as YYYY-MM-DD.
    date: z.coerce.date(),
    // One or two lines shown in lists.
    summary: z.string(),
    // The business the post is about, if one. Sets the marker colour.
    marker: markerSchema.optional(),
    // One photograph, optional.
    image: imageSchema.optional(),
    // Who wrote it.
    author: z.string().optional(),
    // true hides the post from the site while it is being written.
    draft: z.boolean().default(false),
  }),
});

export const collections = { settings, home, businesses, pages, posts };
