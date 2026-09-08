# How to change the website

This guide is for the people who run the farm, not for developers. You do not
need to know code. You need to be able to open a text file, change words, and
save it.

Everything a visitor reads lives in the folder `src/content`. Nothing a
visitor reads lives anywhere else. If you want to change a word on the site,
it is in one of these files:

| File | What it holds |
|---|---|
| `src/content/settings/site.json` | The business name, the five index items, the contact details, the footer, the words the interface uses |
| `src/content/home/home.json` | The home page: the banner slides, the four shelves, About us, Contact |
| `src/content/businesses/livery.md` | The livery and foaling page |
| `src/content/businesses/software.md` | The software page |
| `src/content/businesses/farm.md` | The contracting, beef and lamb page |
| `src/content/businesses/camping.md` | The camping page |
| `src/content/pages/` | Other pages: privacy, cookies, terms. One file each |
| `src/content/posts/` | Blog posts. One file each |

The business pages, the other pages and the post pages are not built yet.
Their content files exist so the words can be written now.

## The rules that keep the site honest

1. **Never make a fact up.** If you do not know something, leave a placeholder
   in this exact form, and it will show on the page until somebody replaces it:

   ```
   [[TODO: what is needed | owner: who supplies it | ref: where it belongs]]
   ```

   For example: `[[TODO: price per week | owner: livery | ref: livery page, prices]]`

2. **Only real photographs.** No stock pictures, nothing from the internet,
   nothing generated. Until a photograph exists the page shows a grey block
   with a note saying what is needed. `IMAGES-NEEDED.md` lists them all.

3. **Say what a thing is.** Plain words, plain verbs, sentence case. Do not
   sell. If a sentence would fit on any other business's website, cut it.

## Two kinds of file

### `.json` files (settings and the home page)

These are lists of names and values. The shape matters:

- Every value that is words sits inside double quotes: `"heading": "Foaling down, night and day"`
- Items in a list are separated by commas. The last item has no comma after it.
- Do not change the names on the left of the colon. Change only the values on the right.
- If you break the shape, the build fails and tells you the line. Nothing goes live broken.

A quote mark inside your words needs a backslash before it: `"She said \"yes\""`.
Easier: use single quotes inside words.

### `.md` files (business pages, other pages, posts)

These have two parts. The top part, between the two `---` lines, is the
settings for the page. The rest is the main text.

```
---
title: First foal of the year
date: 2027-03-02
summary: A colt, born at four in the morning.
---
The main text goes here. As many paragraphs as you like.
```

## Writing text

In any text field, a blank line starts a new paragraph. You may also use:

| To get | Type |
|---|---|
| **bold** | `**bold**` |
| *italic* | `*italic*` |
| a link | `[the words](https://the-address)` or `[the words](/camping/)` |
| a list | Start each line with `- ` |

Nothing else. No headings inside text fields, no pictures inside text fields.
Headings and pictures have their own places.

Short text and long text both work. A field with five words and a field with
five hundred both fit.

## Photographs

A photograph is written like this wherever one is wanted:

```json
"image": {
  "src": "/images/yard-march.jpg",
  "alt": "Four horses at the gate of the top field, early morning",
  "aspect": "landscape"
}
```

- `src` is the file's address. Put the file in the `public/images` folder;
  a file called `yard-march.jpg` in that folder has the address `/images/yard-march.jpg`.
- `alt` says what is in the photograph, for people who cannot see it. Always fill it in.
- `aspect` is the shape of the space: `wide` (the banner), `landscape` (most places), `portrait`, or `square`.
- Until the file exists, leave `src` out and add `brief` (what photograph is wanted)
  and `owner` (who is taking it). The page shows a grey block with the brief.

Sizes: banner photographs at least 2400 pixels wide. Everything else at least
1600 pixels wide. Use JPEG. The build does not yet resize them, so do not
upload a 20 megabyte file.

## The home page, piece by piece

Open `src/content/home/home.json`.

**The banner (`slides`).** Four slides, one per business, in the order they
rotate. Each has a `heading` (keep it under 45 characters so it fits on a
phone), a one-line `text` (under 140 characters), an `image`, and a `link`
button. A longer heading still works; it drops a size.

**The shelves (`shelves`).** Four short blocks under the banner. About 40
words each. `id` is the word in the address bar: `"id": "camping"` means a
link to `/#camping` scrolls to that shelf.

**About us (`about`).** `lead` is the one paragraph that rises into view as
you scroll to it. `blocks` is a stack of page blocks (see below). `posts`
controls the list of latest blog posts: the heading, how many, and what to
say while there are none.

**Contact (`contact`).** A stack of page blocks. Today: the contact facts,
the map and directions, the enquiry form.

## The index and the dial

Open `src/content/settings/site.json` and find `nav`.

`items` are the five index items in ring order, left to right: two
businesses, About us, two businesses. Each has a `label`, an `href`
(where it goes) and a `marker` (which business it is: `livery`, `software`,
`farm` or `camping`). About us has no marker.

`dial` is `true` or `false`. `true` gives the turning dial. `false` gives a
plain row of links. Nothing else changes. If visitors miss the dial, set it
to `false`.

## Page blocks

Business pages, About us and Contact are built by stacking blocks. Each block
has a `type` and its own fields. Put them in the `blocks` list in the order
you want them on the page.

| `type` | What it shows | Fields |
|---|---|---|
| `richText` | Paragraphs | `heading` (optional), `text` |
| `imageText` | A photograph beside paragraphs | `heading` (optional), `text`, `image`, `imageSide` (`left` or `right`), `link` (optional) |
| `gallery` | Photographs to swipe through, with a full-size view on click | `heading` (optional), `images` (a list) |
| `facts` | Label and value pairs | `heading` (optional), `items`: a list of `label` and `value` |
| `prices` | A price table | `heading` (optional), `rows`: a list of `label`, `price`, `detail` (optional); `note` (optional) |
| `questions` | Questions that open to show answers | `heading` (optional), `items`: a list of `question` and `answer` |
| `form` | An enquiry form | `heading`, `text`, `fields`, `submitLabel`, `successText`, `errorText`, `action` (the developer sets this) |
| `cta` | A green band with one button | `heading`, `text` (optional), `link` |
| `quote` | Somebody's words | `text`, `name` (optional), `detail` (optional) |
| `map` | Address, directions and a photograph of the entrance | `heading` (optional), `addressLines`, `directions`, `mapLink` (optional), `image` (optional) |

Every optional field can be left out. Every block works with no photograph,
one photograph, or five.

Example of a facts block:

```json
{
  "type": "facts",
  "heading": "The yard",
  "items": [
    { "label": "Stables", "value": "[[TODO: how many | owner: livery | ref: livery page, facts]]" },
    { "label": "Turnout", "value": "All year, in small groups" }
  ]
}
```

## Adding a page

Make one file. Touch nothing else.

- **A blog post:** copy any file in `src/content/posts/`, or make a new one
  called something like `first-foal-2027.md`. The file name becomes the
  address: `/news/first-foal-2027/`. Fill in `title`, `date` (as
  `YYYY-MM-DD`), `summary`, and the text. Add `marker` to colour it by
  business. Set `draft: true` to keep it off the site while you write.
- **Another page** (privacy, cookies, terms): make a file in
  `src/content/pages/`. The file name is the address: `privacy.md` gives
  `/privacy/`. For the legal pages write the headings and a `[[TODO]]` only.
  A solicitor writes the rest.

The post pages and the other pages are not built yet, so a new file will be
checked by the build but will not show until they are.

## Words the interface uses

Also in `site.json`, under `ui`: the skip link, the scroll cue, the banner
and gallery button labels, the words shown on a missing photograph. Change
them here. They are content like everything else.

## Checking your work

Ask the developer, or if the tools are set up on your computer:

```
npm run build          # checks every file and builds the site. Errors name the file and line.
npm run placeholders   # rewrites PLACEHOLDERS.md and IMAGES-NEEDED.md from the content
npm run dev            # shows the site at http://localhost:4321 while you edit
```

## What not to do

- Do not put words in any file outside `src/content`. They will not be found later.
- Do not paste from Word. Curly quotes break `.json` files. Paste as plain text.
- Do not delete a field to hide it. Leave it empty, or leave it out if the table says it is optional.
- Do not add a new field. The site does not know it and will ignore it, or fail.
