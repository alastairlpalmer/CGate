import { test } from 'node:test';
import assert from 'node:assert/strict';
import { escapeHtml, hasTodo, inline, paragraphs, wordCount } from './text.js';

test('escapes html', () => {
  assert.equal(escapeHtml('<b>&"\'</b>'), '&lt;b&gt;&amp;&quot;&#39;&lt;/b&gt;');
});

test('wraps a placeholder so it stays visible', () => {
  assert.equal(
    inline('Open [[TODO: dates | owner: camping | ref: home]] daily'),
    'Open <mark class="todo">[[TODO: dates | owner: camping | ref: home]]</mark> daily',
  );
  assert.equal(hasTodo('[[TODO: x]]'), true);
  assert.equal(hasTodo('done'), false);
});

test('links, bold and italic', () => {
  assert.equal(inline('See [the yard](/livery/)'), 'See <a href="/livery/">the yard</a>');
  assert.equal(inline('[bad](javascript:alert(1))'), '[bad](javascript:alert(1))');
  assert.equal(inline('**twelve** stables'), '<strong>twelve</strong> stables');
  assert.equal(inline('a *quiet* yard'), 'a <em>quiet</em> yard');
  assert.equal(inline('2 * 3 * 4'), '2 * 3 * 4');
});

test('paragraphs and lists', () => {
  assert.equal(paragraphs('one\n\ntwo'), '<p>one</p>\n<p>two</p>');
  assert.equal(paragraphs('- hay\n- straw'), '<ul><li>hay</li><li>straw</li></ul>');
  assert.equal(paragraphs('line\nbreak'), '<p>line<br>break</p>');
  assert.equal(paragraphs(''), '');
  assert.equal(paragraphs(undefined), '');
});

test('word count', () => {
  assert.equal(wordCount('  five words are in here '), 5);
  assert.equal(wordCount(''), 0);
});
