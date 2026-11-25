// The wide-screen check of C-vet (docs/C_VET_PLAN.md, section 7h).
//
// Run it after cvet_offline_qa.js, on the same server and database: it
// uses the clients, the horse and the visits that run made.
//
//   node tools/qa/cvet_desktop_qa.js http://127.0.0.1:8765 /path/to/out
//
// It checks, at 1440 x 900: the clients stay on the left with one client
// on the right, with its details and its timeline; a horse has
// its history beside the note; "/" goes to the search and the arrows go
// down the list; Ctrl+Enter saves; Prices is a table with a search; a
// visit and a record can be printed; no screen scrolls sideways; a narrow
// window goes back to one part; and a phone does not download the files
// of the wide screen.
const fs = require('fs');
const { chromium } = require('playwright');
const BASE = process.argv[2] || 'http://127.0.0.1:8765';
const OUT = process.argv[3] || 'cvet_qa';
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
  page.on('dialog', (d) => d.accept());
  await page.goto(`${BASE}/accounts/login/`);
  await page.fill('input[name=username]', 'vet'); await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForURL('**/c-vet/'), page.click('button[type=submit]')]);
  await page.click('#download-first');
  await page.locator('#horse-table tr.prow').first().waitFor();
  const r = {};
  r.split = await page.evaluate(() => document.body.classList.contains('split') && !document.getElementById('view-clients').hidden && !document.getElementById('view-client').hidden);
  r.current = await page.locator('#clients .horse[aria-current=true] .strong').textContent();
  await page.screenshot({ path: `${OUT}/d1-clients.png` });
  await page.locator('#clients .horse', { hasText: 'Marsh Stud' }).click();
  r.second = await page.locator('#client-name').textContent();
  // The client's page: its details and its visits, under its horses.
  await page.locator('#client-desk dl').waitFor();
  await page.locator('#client-tabs .seg-tab', { hasText: 'Timeline' }).click();
  await page.locator('#client-timeline details[data-visit]').first().waitFor();
  r.clientPage = (await page.locator('#client-desk dl').textContent()).includes('Sam Marsh') && (await page.locator('#client-timeline details[data-visit]').count()) === 1;
  await page.screenshot({ path: `${OUT}/d1c-client-timeline.png` });
  await page.locator('#client-tabs .seg-tab', { hasText: 'Horses' }).click();
  // "/" goes to the search; the arrow goes into the list; Enter opens.
  await page.locator('#client-name').click();
  await page.keyboard.press('/');
  r.slash = await page.evaluate(() => document.activeElement.id === 'search');
  await page.keyboard.press('ArrowDown');
  r.arrow = await page.evaluate(() => document.activeElement.classList.contains('rowlink') && document.activeElement.textContent.includes('Juniper'));
  await page.screenshot({ path: `${OUT}/d1b-client-page.png` });
  await page.locator('#clients .horse', { hasText: 'Palmer Farm' }).click();
  // The horses are a table: one column for the horse, one for its owner.
  // A filter cuts it, the head of a column sorts it, a group puts owners apart.
  const rows = () => page.locator('#horse-table tr.prow').count();
  const total = await rows();
  r.horseTable = (await page.locator('#horse-table th').allTextContents()).join(',') === 'Horse,Owner,Location,Due' && await page.evaluate(() => getComputedStyle(document.getElementById('horses')).display === 'none');
  await page.selectOption('#horse-owner', { label: 'Jo Bloggs (8)' });
  r.ownerFilter = total === 24 && (await rows()) === 8 && (await page.locator('#horse-filters .found').textContent()).startsWith('8 of 24 horses');
  await page.click('#horse-due-only');
  r.dueFilter = (await rows()) === 1 && (await page.locator('#horse-table tr.prow').textContent()).includes('Willow');
  await page.locator('#horse-filters .link', { hasText: 'Clear filters' }).click();
  await page.locator('#horse-table .sort', { hasText: 'Horse' }).click();
  await page.locator('#horse-table .sort', { hasText: 'Horse' }).click();
  r.sort = (await rows()) === total && (await page.locator('#horse-table tr.prow .rowlink').first().textContent()) === 'Willow' && (await page.locator('#horse-table th[aria-sort=descending]').count()) === 1;
  await page.locator('#horse-table .sort', { hasText: 'Horse' }).click();
  await page.selectOption('#horse-group', 'owner');
  r.groups = (await page.locator('#horse-table tr.band').count()) >= 3 && !(await page.locator('#horse-table th').allTextContents()).includes('Owner');
  await page.screenshot({ path: `${OUT}/d1d-horses-grouped.png` });
  await page.selectOption('#horse-group', '');
  await page.locator('#horse-table .rowlink', { hasText: 'Willow' }).click();
  await page.locator('#history .entry').first().waitFor();
  // The note on the left, the history beside it.
  r.twoColumns = await page.evaluate(() => {
    const note = document.getElementById('note').getBoundingClientRect();
    const history = document.getElementById('history').getBoundingClientRect();
    return document.getElementById('view-clients').hidden && history.left > note.right && history.top < note.bottom;
  });
  r.printRecord = (await page.locator('#print-record').count()) === 1;
  await page.click('#item-add');
  await page.screenshot({ path: `${OUT}/d2-horse-items.png` });
  await page.locator('#picker-list .pick', { hasText: 'Sedation' }).click();
  await page.click('#picker-cancel');
  await page.screenshot({ path: `${OUT}/d3-horse.png`, fullPage: true });
  // Ctrl+Enter saves the note from the keyboard.
  await page.fill('#note', 'Typed at the desk.');
  await page.keyboard.press('Control+Enter');
  await page.waitForFunction(() => !document.getElementById('view-client').hidden);
  r.ctrlEnter = (await page.locator('#client-visit').textContent()).startsWith('Visit open · 1 horse');
  await page.click('.tab[data-view=visits]');
  await page.locator('#past-visits details[data-visit]').first().waitFor();
  await page.locator('#past-visits details[data-visit] > summary').first().click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/d4-visits.png` });
  // Print: only the visit is on the paper, under a line that says whose it is.
  await page.evaluate(() => { window.print = () => { window.printed = { one: document.body.classList.contains('print-one'), head: document.querySelector('.printing .print-head').textContent }; window.dispatchEvent(new Event('afterprint')); }; });
  await page.locator('#past-visits .link', { hasText: 'Print this visit' }).first().click();
  await page.waitForFunction(() => window.printed);
  r.print = await page.evaluate(() => window.printed.one && /^Hill Vets · printed \d+ \w+ \d{4} · visit for /.test(window.printed.head) && !document.querySelector('.print-head'));
  // Invoices: a table, with a search over it.
  await page.click('.tab[data-view=bills]');
  await page.locator('#bill-desk tr.prow').first().waitFor();
  const bills = await page.locator('#bill-desk tr.prow').count();
  await page.fill('#bill-filter', 'juniper');
  r.billTable = bills >= 2 && (await page.locator('#bill-desk tr.prow').count()) === 1 && (await page.locator('#bill-desk tr.prow').textContent()).includes('Marsh Stud');
  await page.fill('#bill-filter', '');
  await page.locator('#bill-desk .rowlink').first().click();
  r.billOpens = (await page.locator('#bill-title').textContent()).startsWith('Invoice ');
  await page.click('#bill-close');
  await page.screenshot({ path: `${OUT}/d4b-invoices.png` });
  // Prices: a table with a search.
  await page.click('.tab[data-view=prices]');
  await page.locator('#prices table').waitFor();
  const all = await page.locator('#prices tr.prow').count();
  await page.keyboard.press('/');
  await page.keyboard.type('vacc');
  r.priceTable = all === 4 && (await page.locator('#prices tr.prow').count()) === 1 && (await page.locator('#prices tr.prow').textContent()).includes('Flu/Tet');
  await page.fill('#price-filter', '');
  await page.selectOption('#price-pick-category', { label: 'Vaccination (1)' });
  r.priceCategory = (await page.locator('#prices tr.prow').count()) === 1;
  await page.locator('#price-filters .link', { hasText: 'Clear filters' }).click();
  await page.locator('#prices .rowlink', { hasText: 'Sedation' }).click();
  await page.locator('#price-form').waitFor();
  r.priceEdit = (await page.inputValue('#price-name')) === 'Sedation';
  await page.click('#price-cancel');
  await page.screenshot({ path: `${OUT}/d5-prices.png` });
  r.sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  // Narrow the window: one part again, same client.
  await page.click('.tab[data-view=clients]');
  await page.setViewportSize({ width: 700, height: 900 });
  await page.waitForTimeout(200);
  r.narrow = await page.evaluate(() => !document.body.classList.contains('split') && document.getElementById('view-clients').hidden && !document.getElementById('view-client').hidden);
  // A phone does not download the files of the wide screen.
  const phone = await (await browser.newContext({ viewport: { width: 390, height: 664 }, storageState: await page.context().storageState() })).newPage();
  await phone.goto(`${BASE}/c-vet/`);
  await phone.waitForFunction(() => document.querySelectorAll('#clients .horse').length > 0 || !document.getElementById('no-clients').hidden);
  r.phoneSkipsDesk = await phone.evaluate(() => !performance.getEntriesByType('resource').some((entry) => entry.name.includes('/cvet/desk.')));
  r.noErrors = errors.length === 0;
  const want = { split: true, current: 'Marsh Stud', second: 'Marsh Stud', clientPage: true, slash: true, arrow: true, twoColumns: true, printRecord: true, ctrlEnter: true, print: true, horseTable: true, ownerFilter: true, dueFilter: true, sort: true, groups: true, billTable: true, billOpens: true, priceTable: true, priceCategory: true, priceEdit: true, sideways: false, narrow: true, phoneSkipsDesk: true, noErrors: true };
  const failed = Object.keys(want).filter((key) => r[key] !== want[key]);
  for (const key of Object.keys(want)) console.log(`${failed.includes(key) ? 'FAIL' : 'PASS'}  ${key}  (${r[key]})`);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
