/*
  Turns editor text into safe HTML.

  Editors write plain paragraphs. A blank line starts a new paragraph.
  Allowed inside a paragraph: **bold**, *italic*, [link words](address),
  and lines starting with "- " for a list. A [[TODO: ...]] placeholder is
  wrapped so it shows on the page. Everything else is shown as typed.

  Plain JavaScript so `node --test` can run text.test.js without a build.
*/

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/** Escape the five characters that mean something in HTML. */
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

const TODO = /\[\[TODO:([^\]]*)\]\]/g;
const LINK = /\[([^\]\n]+)\]\(([^)\s]+)\)/g;
const BOLD = /\*\*([^*\n]+)\*\*/g;
const ITALIC = /(^|[^*\w])\*(\S(?:[^*\n]*\S)?)\*(?![*\w])/g;

/** A link address the page may follow: a path, an anchor, http(s), mailto or tel. */
export function safeHref(href) {
  return /^(\/|#|https?:\/\/|mailto:|tel:)/i.test(href);
}

/** Does the text carry a placeholder? */
export function hasTodo(value) {
  return /\[\[TODO:/.test(String(value ?? ''));
}

/** One line of text: escaped, with placeholders, links, bold and italic. No paragraphs. */
export function inline(value) {
  let out = escapeHtml(value);
  out = out.replace(TODO, (_, body) => `<mark class="todo">[[TODO:${body}]]</mark>`);
  out = out.replace(LINK, (match, label, href) =>
    safeHref(href) ? `<a href="${href}">${label}</a>` : match,
  );
  out = out.replace(BOLD, '<strong>$1</strong>');
  out = out.replace(ITALIC, '$1<em>$2</em>');
  return out;
}

/** Many paragraphs: blank lines separate them; "- " lines make a list. */
export function paragraphs(value) {
  const chunks = String(value ?? '')
    .replace(/\r\n/g, '\n')
    .split(/\n[ \t]*\n+/)
    .map((c) => c.trim())
    .filter(Boolean);

  return chunks
    .map((chunk) => {
      const lines = chunk.split('\n').map((l) => l.trim());
      if (lines.every((l) => l.startsWith('- '))) {
        return `<ul>${lines.map((l) => `<li>${inline(l.slice(2))}</li>`).join('')}</ul>`;
      }
      return `<p>${lines.map(inline).join('<br>')}</p>`;
    })
    .join('\n');
}

/** Roughly how many words. Used to pick a layout for short or long text. */
export function wordCount(value) {
  return String(value ?? '').trim().split(/\s+/).filter(Boolean).length;
}
