// Offline check of C-vet (docs/C_VET_PLAN.md, sections 5 and 7 to 7g).
//
// Drives the app in Chromium the way a day of visits goes: download the
// clients, lose the signal, open the app again, tap the client, treat
// one horse (items from the item list, a changed price, a note, a recheck
// date, a photo, a voice recording), add one item to several horses at
// once, make a new client and its horse with no signal and treat it, read
// each visit folded and unfolded, finish both, get the signal back, sync.
// It also cuts the reply to one upload, to prove that the note is then
// stored once. Then it reads a sent visit back: its note and its recording.
//
//   # on a farrier_seed.py + cvet_seed.py database (see README.md), with C-vet on:
//   CVET_ENABLED=True python manage.py runserver 127.0.0.1:8765
//   node tools/qa/cvet_offline_qa.js http://127.0.0.1:8765 /path/to/out
//
// Then check the rows: the script prints what the server must hold.
//
// This is Chromium on a computer. It does not replace the test on a real
// iPhone (section 8 of the plan): Safari has its own storage and service
// worker rules.

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const { chromium, devices } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8765';
const OUT = process.argv[3] || 'cvet_qa';
const PASSWORD = 'qa-pass-123';
const YARD_NAME = 'Palmer Farm';   // set by cvet_seed.py
// Run the server with CVET_TEST_MODE=False, and this script with the same
// variable, to check the real path: bills with real numbers, and the
// yard's records written. The default is test mode, as on a new server.
const TEST_MODE = process.env.CVET_TEST_MODE !== 'False';
const BILL = TEST_MODE ? 'TEST-CV-0001' : 'CV-0001';
const BILL_2 = TEST_MODE ? 'TEST-CV-0002' : 'CV-0002';

// A 1x1 PNG. The page shrinks it and stores it as a JPEG.
const PHOTO = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail: detail || '' });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
}

async function text(page, selector) {
  return (await page.locator(selector).textContent()).trim();
}

async function tab(page, name) {
  await page.click(`.tab[data-view="${name}"]`);
}

function horseRow(page, name) {
  return page.locator('#horses .horse', { has: page.locator('.strong', { hasText: new RegExp(`^${name}$`) }) });
}

function clientRow(page, name) {
  return page.locator('#clients .horse', { has: page.locator('.strong', { hasText: new RegExp(`^${name}$`) }) });
}

// Clients, then the client: its horses.
async function openClient(page, name) {
  await tab(page, 'clients');
  await clientRow(page, name).click();
  await page.waitForFunction(() => !document.getElementById('view-client').hidden);
}

// The item list: open it, tap each item, close it.
async function addItems(page, ...names) {
  await page.click('#item-add');
  for (const name of names) await page.locator('#picker-list .pick', { hasText: name }).click();
  await page.click('#picker-cancel');
}

// The open visit of one client on the Visits screen, unfolded.
async function openVisit(page, clientName) {
  const visit = page.locator('#open-visits details[data-visit]', { has: page.locator('summary .text', { hasText: clientName }) });
  if (!(await visit.evaluate((node) => node.open))) await visit.locator('> summary').click();
  return visit;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({
    // A fake microphone, and "Allow" answered for us.
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
  });
  const context = await browser.newContext({ ...devices['iPhone 13'], permissions: ['microphone'] });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on('console', (message) => {
    // "Failed to load resource" is the browser reporting a request that
    // found no network, which is what this script does on purpose.
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => consoleErrors.push(String(error)));
  // Every question the app asks ("Finish this visit?") is answered Yes.
  page.on('dialog', (dialog) => dialog.accept());

  // ── Sign in, as the vet does once with a signal. No "next" address: a
  //    Vet-role login must land on C-vet by itself.
  await page.goto(`${BASE}/accounts/login/`);
  await page.fill('input[name=username]', 'vet');
  await page.fill('input[name=password]', PASSWORD);
  await Promise.all([page.waitForURL('**/c-vet/'), page.click('button[type=submit]')]);
  check('a vet login lands on C-vet', new URL(page.url()).pathname === '/c-vet/');
  await page.goto(`${BASE}/health/`);
  check('a vet login cannot open a yard page', new URL(page.url()).pathname === '/c-vet/', page.url());

  // ── The page stores itself.
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => true));
  await tab(page, 'phone');
  await page.waitForFunction(() => document.getElementById('details').textContent.includes('Opens with no signalYes'));
  check('the page is stored on the phone', true);

  // ── Download: clients, horses, due dates, payers, prices.
  await tab(page, 'clients');
  await page.click('#download-first');
  await page.waitForFunction(() => document.querySelectorAll('#clients .horse').length > 0);
  check('the app opens on the clients, and the yard is the first one', (await page.locator('#clients .horse').count()) === 1 && (await clientRow(page, YARD_NAME).count()) === 1);
  check('the client row says what it is and how many are due', /Livery yard · \d+ horses/.test(await clientRow(page, YARD_NAME).textContent()) && (await clientRow(page, YARD_NAME).locator('.due').textContent()) === '2 due', await clientRow(page, YARD_NAME).textContent());
  await page.screenshot({ path: path.join(OUT, '0-clients.png') });
  await openClient(page, YARD_NAME);
  const horses = await page.locator('#horses .horse').count();
  check('a tap on the client shows its horses', horses > 0, `${horses} horses`);
  const firstTwo = await page.locator('#horses .horse .strong').evaluateAll((nodes) => nodes.slice(0, 2).map((n) => n.textContent));
  check('horses with something due are at the top, the overdue one first', firstTwo.join(',') === 'Willow,Fudge', firstTwo.join(','));
  check('the list is headed by the client and its number of horses', (await text(page, '#client-name')) === YARD_NAME && (await text(page, '#client-line')) === `Livery yard · ${horses} horses`, await text(page, '#client-line'));
  check('the yard and its horses are not changed from C-vet', (await page.locator('#client-edit').isHidden()) && (await page.locator('#horse-add').isHidden()));
  check('the overdue horse says so', (await text(page, '#horses .horse >> nth=0 >> .due')).startsWith('Flu/Tet overdue'), await text(page, '#horses .horse >> nth=0 >> .due'));
  await page.screenshot({ path: path.join(OUT, '1-horses.png') });

  // ── Lose the signal and open the app again.
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#clients .horse').length > 0);
  check('the app opens with no signal', (await text(page, 'h1')).startsWith('C-vet'));
  // A vet may know the horse and not the client: the search finds both.
  await page.fill('#client-search', 'will');
  check('the search on Clients finds a horse and names its client', (await page.locator('#found-horses .horse').count()) === 1 && (await page.locator('#found-horses .horse small').first().textContent()).startsWith(YARD_NAME));
  await page.fill('#client-search', '');
  await openClient(page, YARD_NAME);
  check('the horse list is still there with no signal', (await page.locator('#horses .horse').count()) === horses);
  check('the top bar says No signal', (await page.locator('#signal').isVisible()) && (await text(page, '#signal')) === 'No signal');
  await page.fill('#search', 'bram');
  check('search works with no signal', (await page.locator('#horses .horse').count()) === 1);
  await page.fill('#search', '');

  // ── Treat one horse, with no signal.
  await horseRow(page, 'Willow').click();
  check('the horse screen names the owner', (await text(page, '#horse-payers')) === 'Owner: Jo Bloggs', await text(page, '#horse-payers'));
  check('the horse screen says what is due', (await text(page, '#horse-due')).includes('Flu/Tet overdue'));
  // The item list: a search, the categories, and the prices under them.
  await page.click('#item-add');
  check('the item list is grouped by category', (await page.locator('#picker-list h3').allTextContents()).join(',') === 'Vaccination,Other', (await page.locator('#picker-list h3').allTextContents()).join(','));
  await page.fill('#picker-search', 'sed');
  check('the search in the item list finds an item', (await page.locator('#picker-list .pick').count()) === 1);
  await page.locator('#picker-list .pick', { hasText: 'Sedation' }).click();
  await page.fill('#picker-search', '');
  await page.locator('#picker-categories .tile', { hasText: 'Vaccination' }).click();
  check('a category shows its items only', (await page.locator('#picker-list .pick').count()) === 1);
  await page.locator('#picker-list .pick', { hasText: 'Flu/Tet booster' }).click();
  check('the list stays open and counts what was added', (await page.locator('#picker-list .pick .count').textContent()) === '× 1');
  await page.screenshot({ path: path.join(OUT, '2-items.png') });
  await page.click('#picker-cancel');
  check('the items are on the note', (await page.locator('#lines > li').count()) === 2);
  // The booster costs less for this horse: change the price on the line.
  await page.locator('#lines input[aria-label="Price for Flu/Tet booster"]').fill('60');
  check('the total follows the items and a changed price', (await text(page, '#lines-total')) === 'Total £87.50', await text(page, '#lines-total'));
  // The note, as dictation types it. The app offers the recheck date
  // from its words; one tap uses it.
  check('no recheck is offered before the note asks for one', await page.locator('#recheck-suggestion').isHidden());
  await page.fill('#note', 'Mild heat left fore, no lameness at trot. Recheck in two weeks.');
  await page.waitForFunction(() => !document.getElementById('recheck-suggestion').hidden);
  check('the app offers a recheck date from the words of the note', (await text(page, '#recheck-suggestion')) === 'Use 30 Nov: you said “Recheck in two weeks”' || /Use \d+ \w+: you said “Recheck in two weeks”/.test(await text(page, '#recheck-suggestion')), await text(page, '#recheck-suggestion'));
  await page.click('#recheck-suggestion');
  check('one tap uses it', (await page.locator('#followups .tile.on').textContent()) === '2 wk' && await page.locator('#recheck-suggestion').isHidden());
  await page.setInputFiles('#photo', { name: 'leg.png', mimeType: 'image/png', buffer: PHOTO });
  await page.waitForFunction(() => document.getElementById('photo-state').textContent.startsWith('Photo ready'));
  // A photo taken by mistake comes off again.
  await page.click('#photo-state .link');
  check('a photo can be removed', (await text(page, '#photo-state')) === '');
  await page.setInputFiles('#photo', { name: 'leg.png', mimeType: 'image/png', buffer: PHOTO });
  await page.waitForFunction(() => document.getElementById('photo-state').textContent.startsWith('Photo ready'));

  // A save that is refused says why next to Save, and goes to the box.
  await page.locator('#lines input[aria-label="Price for Sedation"]').fill('abc');
  await page.click('#save');
  check('a refused save says why, in view', (await text(page, '#entry-message')).includes('not a number') && await page.locator('#entry-message').isVisible());
  check('and puts the cursor in the wrong box', await page.evaluate(() => document.activeElement.getAttribute('aria-label') === 'Price for Sedation'));
  check('nothing was saved', (await text(page, '#unsent')) === 'All sent');
  await page.locator('#lines input[aria-label="Price for Sedation"]').fill('27.50');

  // The phone reloads the app in the middle of the note (an iPhone can,
  // after the camera). The note comes back as it was.
  await page.waitForTimeout(500);
  await page.reload();
  await page.waitForFunction(() => !document.getElementById('view-horse').hidden);
  const back = await page.evaluate(() => ({
    horse: document.getElementById('horse-name').textContent,
    note: document.getElementById('note').value,
    total: document.getElementById('lines-total').textContent,
    recheck: document.getElementById('follow-up').value !== '',
    photo: document.getElementById('photo-state').textContent.startsWith('Photo ready'),
  }));
  check('a note being written survives a reload of the app, with no signal', back.horse === 'Willow' && back.note.startsWith('Mild heat') && back.total === 'Total £87.50' && back.recheck && back.photo, JSON.stringify(back));
  check('Save stays in view at the foot of the screen', await page.evaluate(() => {
    const box = document.getElementById('save').getBoundingClientRect();
    return box.top > 0 && box.bottom <= document.querySelector('.tabs').getBoundingClientRect().top + 1;
  }));
  await page.click('#record');
  await page.waitForFunction(() => document.getElementById('record').textContent === 'Stop recording');
  await page.waitForTimeout(1200);
  await page.click('#record');
  await page.waitForFunction(() => document.getElementById('audio-state').textContent.startsWith('Voice recorded'));
  await page.screenshot({ path: path.join(OUT, '2-horse.png'), fullPage: true });
  await page.click('#save');
  await page.waitForFunction(() => !document.getElementById('view-client').hidden);
  check('after Save the vet is back on the horses of the client', (await text(page, '#client-name')) === YARD_NAME);
  check('the client shows its open visit in one line', (await text(page, '#client-visit')) === 'Visit open · 1 horse · £87.50', await text(page, '#client-visit'));
  check('the counter shows 1 note not sent', (await text(page, '#unsent')) === '1 note not sent', await text(page, '#unsent'));
  check('the treated horse has a tick', (await horseRow(page, 'Willow').locator('.mark.done').count()) === 1);

  // ── One item for several horses at once.
  await page.click('#select-toggle');
  for (const name of ['Bramble', 'Clover', 'No Owner Ned']) await horseRow(page, name).click();
  check('3 horses selected', (await text(page, '#select-count')) === '3 horses selected', await text(page, '#select-count'));
  await page.click('#select-add');
  check('the item list says how many horses', (await text(page, '#picker-title')) === 'Add item to 3 horses');
  await page.locator('#picker-list .pick', { hasText: 'Flu/Tet booster' }).click();
  await page.waitForFunction(() => document.getElementById('unsent').textContent === '4 notes not sent');
  check('one item added to 3 horses in one step', true, await text(page, '#sync-message'));

  // ── A new client and its horse, made at its yard with no signal.
  await tab(page, 'clients');
  await page.click('#client-add');
  await page.fill('#client-form-name', 'Marsh Stud');
  await page.selectOption('#client-form-kind', 'stud');
  await page.fill('#client-form-contact', 'Sam Marsh');
  await page.screenshot({ path: path.join(OUT, '3-client-form.png') });
  await page.click('#client-form button[type=submit]');
  await page.waitForFunction(() => document.getElementById('client-name').textContent === 'Marsh Stud');
  check('a new client is made with no signal, and opens', (await text(page, '#client-line')) === 'Stud · 0 horses', await text(page, '#client-line'));
  await page.click('#horse-add');
  await page.fill('#horse-form-name', 'Juniper');
  await page.fill('#horse-form-owner', 'Sam Marsh');
  await page.click('#horse-form button[type=submit]');
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length === 1);
  check('a horse is added to the new client with no signal', (await horseRow(page, 'Juniper').textContent()).includes('not sent yet'));
  check('the counter says there are changes to send', (await text(page, '#unsent')) === '4 notes not sent');
  await horseRow(page, 'Juniper').click();
  check('the horse screen goes back to its client', (await text(page, '#back')) === '‹ Marsh Stud' && (await text(page, '#horse-payers')) === 'Owner: Sam Marsh');
  await addItems(page, 'Visit fee');
  await page.fill('#note', 'In foal. Scan again in a month.');
  await page.click('#save');
  await page.waitForFunction(() => document.getElementById('unsent').textContent === '5 notes not sent');

  // ── Visits: one open visit for each client, each folded to a line.
  await tab(page, 'visits');
  check('each client has its own open visit', (await page.locator('#open-visits details[data-visit]').count()) === 2 && (await text(page, '#visit-badge')) === '2');
  const yardVisit = await openVisit(page, YARD_NAME);
  const studVisit = await openVisit(page, 'Marsh Stud');
  check('a folded visit says the client, the horses and the total', (await yardVisit.locator('> summary').textContent()).includes('4 horses') && (await yardVisit.locator('> summary .amount').textContent()) === '£282.50', await yardVisit.locator('> summary').textContent());
  check('the summary lists 4 horses and the total', (await yardVisit.locator('.total').textContent()) === '4 horses · Total £282.50', await yardVisit.locator('.total').textContent());
  // The practice's client is the yard: one bill, to the yard, which
  // invoices each owner. No split between owners on the vet's screen.
  check('the summary says the bill goes to the client', (await yardVisit.locator('.body > p.muted').first().textContent()) === `Bill to ${YARD_NAME}` && (await studVisit.locator('.body > p.muted').first().textContent()) === 'Bill to Marsh Stud');
  check('no split between owners when the yard is billed', (await yardVisit.locator('.pay').count()) === 0);
  // Each horse of a visit is one line. A tap unfolds its items and notes.
  const willow = yardVisit.locator('details.inner', { has: page.locator('summary .text', { hasText: 'Willow' }) });
  const folded = await willow.locator('summary small').textContent();
  check('a horse of a visit is folded to one line', /^2 items · note · photo · voice · not sent yet · recheck \d+ \w+/.test(folded) && !(await willow.evaluate((node) => node.open)), folded);
  await willow.locator('summary').click();
  await willow.locator('.entry').waitFor();
  check('unfolded, the horse shows its items and the words of the note', (await willow.locator('.entry .item').count()) === 2 && (await willow.locator('.entry .words').textContent()).startsWith('Mild heat left fore'));
  await willow.locator('.file .link', { hasText: 'Play voice note' }).click();
  await willow.locator('audio').waitFor();
  check('a recording that is not sent yet plays from the phone, with no signal', (await willow.locator('audio').getAttribute('src')).startsWith('blob:'));
  const bramble = yardVisit.locator('details.inner', { has: page.locator('summary .text', { hasText: 'Bramble' }) });
  await bramble.locator('summary').click();
  const owners = await bramble.locator('.body > small').last().textContent();
  check('each horse still names its owners, for the yard', owners === 'Owners: Jo Bloggs 50% · Ali Khan 50%', owners);
  const cutShort = await page.locator('#view-visits .amount').evaluateAll((nodes) => nodes.filter((n) => n.scrollWidth > n.clientWidth + 1).length);
  check('no amount of money is cut short on the summary', cutShort === 0, `${cutShort} cut`);
  const finishWidth = await yardVisit.locator('[data-finish]').evaluate((node) => node.getBoundingClientRect().width / window.innerWidth);
  check('Finish visit is a wide button', finishWidth > 0.8, `${Math.round(finishWidth * 100)}% of the screen`);
  await page.screenshot({ path: path.join(OUT, '3-visits.png'), fullPage: true });

  // ── Sync by hand with no signal: nothing is lost.
  await page.click('#unsent');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent.includes('No signal'));
  check('sync with no signal keeps every note', (await text(page, '#unsent')) === '5 notes not sent');

  // ── Close the app and open it again, still with no signal.
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#clients .horse').length > 0);
  check('the new client is still there after the app is opened again', (await clientRow(page, 'Marsh Stud').locator('.pill').textContent()) === 'Visit open');
  await tab(page, 'visits');
  check('the visits are still there after the app is opened again', (await (await openVisit(page, YARD_NAME)).locator('.total').textContent()) === '4 horses · Total £282.50');

  // ── Finish both visits, still with no signal.
  for (const name of ['Marsh Stud', YARD_NAME]) {
    await (await openVisit(page, name)).locator('[data-finish]').click();
    await page.waitForFunction((client) => [...document.querySelectorAll('#past-visits details[data-visit] > summary .text')].some((node) => node.textContent.startsWith(client)), name);
  }
  check('the visits are finished with no signal, and say they are not sent', (await page.locator('#open-visits details').count()) === 0 && (await page.locator('#past-visits details[data-visit] > summary small').allTextContents()).every((line) => line.includes('not all sent yet')));

  // ── The signal is back, but the reply to the first upload is lost: the
  //    server stores the note and the phone never hears about it. Nobody
  //    taps Sync here: the app sends by itself when the signal comes back.
  let cut = 0;
  await context.route('**/c-vet/api/notes/', async (route) => {
    if (route.request().method() === 'POST' && cut === 0) {
      cut += 1;
      await route.fetch();     // the server stores the note...
      await route.abort();     // ...and the reply never arrives
      return;
    }
    await route.continue();
  });
  await context.setOffline(false);
  for (let i = 0; i < 100 && cut === 0; i += 1) await page.waitForTimeout(100);
  check('the app sends by itself when the signal is back', cut === 1);
  await page.waitForFunction(() => !document.getElementById('sync').disabled);
  check('a lost reply leaves the note marked not sent', (await text(page, '#unsent')) === '5 notes not sent', await text(page, '#unsent'));

  // ── Sync again. The first note goes a second time; the server must
  //    answer "duplicate". Then the finished visit goes.
  await page.click('#unsent');
  await page.waitForFunction(() => document.getElementById('unsent').textContent === 'All sent', null, { timeout: 30000 });
  check('everything is sent after the second sync, the finished visits too', true);
  // The server made a bill for each client when its visit was finished.
  // In test mode a bill has a TEST number. The stud's visit went first.
  await page.waitForFunction((bill) => [...document.querySelectorAll('#past-visits details[data-visit] > summary small')].some((node) => node.textContent.includes(bill)), BILL_2);
  const lines = await page.locator('#past-visits details[data-visit] > summary').allTextContents();
  check('each finished visit shows the bill the server made', lines.some((line) => line.includes(YARD_NAME) && line.includes(`Bill ${BILL_2}`) && line.includes('£282.50')) && lines.some((line) => line.includes('Marsh Stud') && line.includes(`Bill ${BILL}`) && line.includes('£40.00')), lines.join(' | '));

  // ── The record of a visit that was sent: the note and the recording.
  //    The phone gave its copy of the files back; they come from the
  //    practice's records now.
  const past = page.locator('#past-visits details[data-visit]', { has: page.locator('summary .text', { hasText: YARD_NAME }) });
  if (!(await past.evaluate((node) => node.open))) await past.locator('> summary').click();
  const sentWillow = past.locator('details.inner', { has: page.locator('summary .text', { hasText: 'Willow' }) });
  await sentWillow.waitFor();
  if (!(await sentWillow.evaluate((node) => node.open))) await sentWillow.locator('summary').click();
  check('a sent visit still shows the words of its note', (await sentWillow.locator('.entry .words').textContent()).startsWith('Mild heat left fore'));
  await sentWillow.locator('.file .link', { hasText: 'Play voice note' }).click();
  await sentWillow.locator('audio').waitFor();
  const played = await sentWillow.locator('audio').evaluate((node) => new Promise((resolve) => {
    if (node.readyState >= 1) resolve(node.duration > 0 || node.readyState);
    node.addEventListener('loadedmetadata', () => resolve(true));
    node.addEventListener('error', () => resolve(false));
  }));
  check('the recording of a sent note plays, from the practice\'s records', !!played);
  await sentWillow.locator('.file .link', { hasText: 'Show photo' }).click();
  await sentWillow.locator('img').waitFor();
  check('and its photo shows', (await sentWillow.locator('img').evaluate((node) => node.decode().then(() => node.naturalWidth > 0, () => false))));
  check('the bill of the visit is one tap away', (await past.locator('a.button', { hasText: BILL_2 }).getAttribute('href')).startsWith('/c-vet/bills/'));
  await page.screenshot({ path: path.join(OUT, '4-visit-record.png'), fullPage: true });

  // ── Sync once more: nothing goes again.
  let extra = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/c-vet/api/')) extra += 1;
  });
  await page.click('#unsent');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent === 'Nothing to send.');
  check('a third sync sends nothing', extra === 0, `${extra} uploads`);

  // ── What the practice's records hold now.
  const stored = await page.evaluate(async () => {
    const reply = await fetch('/c-vet/api/notes/', { credentials: 'same-origin' });
    const notes = (await reply.json()).notes;
    const willow = notes.find((note) => note.horse_name === 'Willow');
    const juniper = notes.find((note) => note.horse_name === 'Juniper');
    const yard = await (await fetch('/c-vet/api/yard/', { credentials: 'same-origin' })).json();
    const file = await fetch(willow.files[0].url, { credentials: 'same-origin' });
    return {
      count: notes.length,
      allTest: notes.every((note) => note.is_test === true),
      lines: notes.reduce((sum, note) => sum + note.lines.length, 0),
      total: notes.reduce((sum, note) => sum + note.lines.reduce((s, line) => s + Number(line.total), 0), 0),
      willowLines: willow.lines.map((line) => `${line.description} ${line.total}`).join(', '),
      willowRecheck: willow.follow_up_date,
      willowFiles: willow.files.length,
      fileStatus: file.status,
      fileBytes: (await file.arrayBuffer()).byteLength,
      // The client and the horse that were made with no signal.
      clients: yard.clients.map((client) => `${client.name} (${client.kind})`).join(', '),
      juniperOwn: !!juniper && juniper.horse_id === null && !!juniper.client_horse_id,
      ownHorses: yard.horses.filter((horse) => horse.own).map((horse) => `${horse.name}/${horse.owner}`).join(','),
    };
  });
  check('the practice holds 5 notes with 6 items', stored.count === 5 && stored.lines === 6, `${stored.count} notes, ${stored.lines} items`);
  check('the stored total is the total on the phone', stored.total === 322.5, `£${stored.total}`);
  check('the practice holds the new client and its horse', stored.clients === 'Marsh Stud (stud), Palmer Farm (yard)' && stored.ownHorses === 'Juniper/Sam Marsh' && stored.juniperOwn, `${stored.clients}; ${stored.ownHorses}`);
  check('the changed price was stored for that horse only', stored.willowLines === 'Sedation 27.50, Flu/Tet booster 60.00', stored.willowLines);
  check('the recheck date was stored', /^\d{4}-\d{2}-\d{2}$/.test(stored.willowRecheck || ''), stored.willowRecheck);
  check('the photo and the recording were stored and download complete', stored.willowFiles === 2 && stored.fileStatus === 200 && stored.fileBytes > 0);
  check(TEST_MODE ? 'the notes are marked as test notes' : 'the notes are real notes', stored.allTest === TEST_MODE);
  check(TEST_MODE ? 'the screen says the notes are test notes' : 'the screen does not say test notes', (await page.locator('#test-mode').isVisible()) === TEST_MODE);

  // ── History on the horse screen, and a new download shows the recheck.
  await tab(page, 'phone');
  await page.click('#download');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent === 'Clients and horses downloaded.');
  check('the new client and its horse are sent: nothing waits', (await text(page, '#unsent')) === 'All sent');
  await openClient(page, YARD_NAME);
  await horseRow(page, 'Willow').click();
  check('the horse now shows its recheck as due', (await text(page, '#horse-due')).includes('Recheck due'), await text(page, '#horse-due'));
  // The practice's record of the horse loads by itself, with a signal.
  await page.locator('#history .file .link', { hasText: 'Play voice note' }).waitFor();
  check('the history shows the note once, with its photo and recording to open', (await page.locator('#history .entry').count()) === 1 && (await page.locator('#history .file .link').count()) === 2);
  await page.screenshot({ path: path.join(OUT, '4-horse-history.png'), fullPage: true });
  // One tap for the items of the last note: at today's prices.
  check('the horse offers the items of its last note', (await text(page, '#repeat')).includes('Sedation, Flu/Tet booster'), await text(page, '#repeat'));
  await page.click('#repeat');
  check('one tap puts them on the note, at the price of the list', (await page.locator('#lines > li').count()) === 2 && (await text(page, '#lines-total')) === 'Total £92.50' && await page.locator('#repeat').isHidden(), await text(page, '#lines-total'));
  for (let i = 0; i < 2; i += 1) await page.locator('#lines .step').first().click();
  check('the history is a timeline: the day, then its notes', (await text(page, '#history .day')) === 'Today' && (await page.locator('#history .timeline .entry').count()) === 1);
  await page.click('#back');

  // ── One client, three ways: horses, timeline, invoices.
  await page.locator('#client-tabs .seg-tab', { hasText: 'Timeline' }).click();
  check('the client\'s timeline has what is due and its visits only', (await page.locator('#client-timeline details[data-visit]').count()) === 1 && (await page.locator('#client-timeline .list .horse').count()) >= 2 && (await text(page, '#client-timeline h3 >> nth=0')) === 'Due');
  await page.locator('#client-tabs .seg-tab', { hasText: 'Invoices' }).click();
  await page.locator('#client-bills .horse').first().waitFor();
  check('the client\'s invoices: its bill, not paid, and the amount to pay', (await page.locator('#client-bills .horse').count()) === 1 && (await text(page, '#client-bills .horse')).includes(BILL_2) && (await text(page, '#client-bills .horse .due')) === 'Not paid' && (await text(page, '#client-bills .count.hot')) === '£282.50 to pay', await text(page, '#client-bills'));
  check('the tab counts the invoices that are not paid', (await text(page, '#client-tabs .seg-tab >> nth=2')) === 'Invoices1');

  // ── Invoices: every bill, paid or not, with no page to load.
  await tab(page, 'bills');
  await page.locator('#bills .horse').first().waitFor();
  check('Invoices lists both bills and what is to pay', (await page.locator('#bills .horse').count()) === 2 && (await text(page, '#bill-counts .count.hot')) === '£322.50 to pay', await text(page, '#bill-counts'));
  await page.locator('#bills .horse', { hasText: BILL }).click();
  check('an invoice opens with each horse\'s items and the total', (await text(page, '#bill-title')) === `Invoice ${BILL}` && (await text(page, '#bill-body')).includes('Juniper') && (await text(page, '#bill-body .total')) === 'Total £40.00' && !(await text(page, '#bill-body')).includes('In foal'), await text(page, '#bill-body'));
  await page.locator('#bill-body button', { hasText: 'Mark as paid today' }).click();
  await page.locator('#bill-body button', { hasText: 'Mark as not paid' }).waitFor();
  await page.click('#bill-close');
  check('one tap marks it paid, and the figures follow', (await text(page, '#bill-counts .count.hot')) === '£282.50 to pay' && (await page.locator('#bills .horse', { hasText: BILL }).locator('.pill.done').count()) === 1, await text(page, '#bill-counts'));
  await page.locator('#bill-chips .tile', { hasText: 'Not paid' }).click();
  check('the list is cut to what is not paid', (await page.locator('#bills .horse').count()) === 1 && (await text(page, '#bills .horse')).includes(BILL_2));
  await page.locator('#bill-chips .tile', { hasText: 'All' }).click();
  await page.screenshot({ path: path.join(OUT, '4-invoices.png'), fullPage: true });

  // ── The vet adds a price.
  await tab(page, 'prices');
  check('the booster is marked as a vaccination', (await page.locator('#prices .price', { hasText: 'Flu/Tet booster' }).textContent()).includes('records a vaccination'));
  await page.click('#price-add');
  // The code for a price change comes with the first use: it needs a signal.
  await page.locator('#price-form').waitFor();
  check('a price can be marked as one of the yard\'s vaccinations', (await page.locator('#price-vaccination option').allTextContents()).join(',') === 'No,Flu/Tet');
  await page.fill('#price-name', 'Dental check');
  await page.fill('#price-category', 'Dental');
  await page.fill('#price-amount', 'forty');
  await page.click('#price-save');
  check('a price that is not a number is refused, with the reason', (await text(page, '#price-message')).includes('as a number'));
  await page.fill('#price-amount', '48');
  await page.click('#price-save');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent === 'Dental check saved.');
  check('the vet adds a price, under its category', (await page.locator('#prices .price', { hasText: 'Dental check' }).count()) === 1 && (await page.locator('#prices h3').allTextContents()).join(',') === 'Dental,Vaccination,Other', (await page.locator('#prices h3').allTextContents()).join(','));
  await page.screenshot({ path: path.join(OUT, '4-prices.png'), fullPage: true });

  // ── Compact and fast (section 5). Open the app again with no signal
  //    and measure it.
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#clients .horse').length > 0);
  const ready = await page.evaluate(() => Math.round(performance.now()));
  await openClient(page, YARD_NAME);
  const speed = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#horses .horse')];
    const floor = document.querySelector('.tabs').getBoundingClientRect().top;
    const visible = rows.filter((row) => row.getBoundingClientRect().bottom <= floor).length;
    const small = [...document.querySelectorAll('#horses .horse, .tab, #select-toggle, #search')]
      .filter((node) => node.getBoundingClientRect().height < 44).length;
    return { visible, small, height: window.innerHeight };
  });
  check('opens with no signal in under 1 second', ready < 1000, `${ready} ms to the clients`);
  check('8 or more horses on the first screen, above the tabs', speed.visible >= 8, `${speed.visible} on a ${speed.height} px screen`);
  check('rows, tabs and buttons are 44 px or more to tap', speed.small === 0, `${speed.small} too small`);
  // What the phone downloads, once: the page's files as the server
  // sends them (compressed). And as written, which is what it stores.
  const files = await page.evaluate(() => performance.getEntriesByType('resource')
    .map((entry) => entry.name).filter((name) => name.includes('/static/cvet/')));
  await context.setOffline(false);
  let written = 0;
  let sent = 0;
  for (const url of [...new Set(files)]) {
    const body = await (await context.request.get(url)).body();
    written += body.length;
    sent += zlib.gzipSync(body).length;
  }
  await context.setOffline(true);
  check('page files are under 50 KB as the phone downloads them', files.length >= 3 && sent < 50 * 1024, `${Math.round(sent / 1024)} KB compressed`);
  check('and under 200 KB as written', written < 200 * 1024, `${Math.round(written / 1024)} KB`);
  await page.screenshot({ path: path.join(OUT, '5-compact.png') });
  await context.setOffline(false);

  // ── A long horse name is shown in full, and Select hides no horse.
  const longName = await horseRow(page, 'Thunderbolt Magnificent Champion of Wettenhall').locator('.strong').evaluate((node) => node.scrollWidth <= node.clientWidth + 1);
  check('a long horse name is not cut short', longName);
  await page.click('#select-toggle');
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const lastHidden = await page.evaluate(() => {
    const rows = document.querySelectorAll('#horses .horse');
    return rows[rows.length - 1].getBoundingClientRect().bottom > document.getElementById('select-bar').getBoundingClientRect().top + 1;
  });
  check('in Select, the last horse is not under the bar', !lastHidden);
  await page.click('#select-toggle');
  await page.evaluate(() => window.scrollTo(0, 0));

  // ── On the smallest phone (320 px) the top bar is one line.
  await page.setViewportSize({ width: 320, height: 568 });
  const barHeight = await page.locator('.bar').evaluate((node) => node.getBoundingClientRect().height);
  check('the top bar is one line on a 320 px phone', barHeight < 48, `${Math.round(barHeight)} px high`);
  check('no sideways scroll on a 320 px phone', !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)));
  await page.setViewportSize({ width: 390, height: 664 });

  // ── Every screen fits a phone.
  let wide = [];
  for (const name of ['clients', 'visits', 'bills', 'prices', 'phone']) {
    await tab(page, name);
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) wide.push(name);
  }
  await openClient(page, YARD_NAME);
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) wide.push('client');
  await horseRow(page, 'Thunderbolt Magnificent Champion of Wettenhall').click();
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) wide.push('horse');
  await page.click('#back');
  check('no sideways scroll on any screen of an iPhone 13', wide.length === 0, wide.join(','));
  check('no script errors', consoleErrors.length === 0, consoleErrors.join(' | ').slice(0, 300));

  // ── The practice's bills: a page, with a signal.
  await tab(page, 'phone');
  await Promise.all([page.waitForURL('**/c-vet/bills/'), page.click('a[href="/c-vet/bills/"]')]);
  const billTable = await page.locator('table').textContent();
  check('the bills page lists a bill for each client', [BILL, BILL_2, 'Marsh Stud', 'Palmer Farm'].every((t) => billTable.includes(t)));
  await page.click(`a:has-text("${BILL_2}")`);
  const billPage = await page.locator('body').textContent();
  check('the bill names the yard, each horse and the total', ['Palmer Farm', 'Willow', 'Bramble', 'No Owner Ned', '£282.50'].every((t) => billPage.includes(t)) && !billPage.includes('Juniper') && billPage.includes('Test bill') === TEST_MODE);
  check('the bill does not carry the clinical note', !billPage.includes('Mild heat'));
  await page.screenshot({ path: path.join(OUT, '6-bill.png'), fullPage: true });
  await page.goto(`${BASE}/c-vet/`);
  await page.waitForFunction(() => document.querySelectorAll('#clients .horse').length > 0);

  // ── Sign out: the phone is emptied and the sign-in ends.
  await tab(page, 'phone');
  await Promise.all([page.waitForURL('**/accounts/login/**'), page.click('#sign-out')]);
  const stores = await page.evaluate(() => caches.keys());
  await page.goto(`${BASE}/c-vet/`);
  check('sign out ends the sign-in', new URL(page.url()).pathname === '/accounts/login/', page.url());
  const after = await page.evaluate(() => new Promise((resolve) => {
    const open = indexedDB.open('cvet');
    open.onsuccess = () => {
      const db = open.result;
      if (!db.objectStoreNames.contains('horses')) { resolve(0); return; }
      const tx = db.transaction(['horses', 'entries', 'prices', 'clients']);
      const counts = [];
      for (const name of ['horses', 'entries', 'prices', 'clients']) {
        const count = tx.objectStore(name).count();
        count.onsuccess = () => { counts.push(count.result); if (counts.length === 4) resolve(counts.reduce((a, b) => a + b, 0)); };
      }
    };
    open.onerror = () => resolve(-1);
  }));
  check('sign out removes clients, horses, notes and prices from the phone', after === 0, `${after} rows left`);
  check('sign out removes the stored page', stores.filter((name) => name.startsWith('cvet-')).length === 0, stores.join(','));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(results, null, 2));
  console.log(`\n${results.length - failed.length} of ${results.length} checks passed.`);
  console.log('The server must now hold: 2 clients (the yard and Marsh Stud), 1 horse of its own (Juniper), 5 clinical notes, 6 items (total 322.50), 2 files, 5 receipts (1 with attempts = 2), 2 finished visits, 2 bills (' + BILL + ' to Marsh Stud, ' + BILL_2 + ' to the yard), 4 prices. ' + (TEST_MODE
    ? 'Test mode: no vet record, charge or cost at the yard.'
    : 'At the yard: 4 vaccination records, 1 vet visit record (Willow: sedation and the recheck), charges for 3 horses (No Owner Ned has no owner), and 1 cost for the yard\'s bill. Nothing of Marsh Stud is at the yard.'));
  process.exit(failed.length ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
