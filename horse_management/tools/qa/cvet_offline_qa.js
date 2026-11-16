// Offline check of C-vet (docs/C_VET_PLAN.md, sections 5 and 7 to 7c).
//
// Drives the app in Chromium the way a yard visit goes: download the
// yard, lose the signal, open the app again, treat one horse (items, a
// changed price, a note, a recheck date, a photo, a voice recording), add
// one item to several horses at once, read the summary, finish the visit,
// get the signal back, sync. It also cuts the reply to one upload, to
// prove that the note is then stored once.
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

  // ── Download the yard: horses, due dates, payers, prices.
  await tab(page, 'horses');
  await page.click('#download-first');
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  const horses = await page.locator('#horses .horse').count();
  check('horse list downloaded', horses > 0, `${horses} horses`);
  const firstTwo = await page.locator('#horses .horse .strong').evaluateAll((nodes) => nodes.slice(0, 2).map((n) => n.textContent));
  check('horses with something due are at the top, the overdue one first', firstTwo.join(',') === 'Willow,Fudge', firstTwo.join(','));
  check('the list is headed by the yard and its number of horses', (await text(page, '#yard-line')) === `${YARD_NAME} · ${horses} horses`, await text(page, '#yard-line'));
  check('the overdue horse says so', (await text(page, '#horses .horse >> nth=0 >> .due')).startsWith('Flu/Tet overdue'), await text(page, '#horses .horse >> nth=0 >> .due'));
  await page.screenshot({ path: path.join(OUT, '1-horses.png') });

  // ── Lose the signal and open the app again.
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  check('the app opens with no signal', (await text(page, 'h1')).startsWith('C-vet'));
  check('the horse list is still there with no signal', (await page.locator('#horses .horse').count()) === horses);
  check('the top bar says No signal', (await page.locator('#signal').isVisible()) && (await text(page, '#signal')) === 'No signal');
  await page.fill('#search', 'bram');
  check('search works with no signal', (await page.locator('#horses .horse').count()) === 1);
  await page.fill('#search', '');

  // ── Treat one horse, with no signal.
  await horseRow(page, 'Willow').click();
  check('the horse screen names the owner', (await text(page, '#horse-payers')) === 'Owner: Jo Bloggs', await text(page, '#horse-payers'));
  check('the horse screen says what is due', (await text(page, '#horse-due')).includes('Flu/Tet overdue'));
  await page.locator('#tiles .tile', { hasText: 'Flu/Tet booster' }).click();
  await page.locator('#tiles .tile', { hasText: 'Sedation' }).click();
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
  await page.waitForFunction(() => !document.getElementById('view-horses').hidden);
  check('after Save the vet is back on the horse list', true);
  check('the counter shows 1 note not sent', (await text(page, '#unsent')) === '1 note not sent', await text(page, '#unsent'));
  check('the treated horse has a tick', (await horseRow(page, 'Willow').locator('.mark.done').count()) === 1);

  // ── One item for several horses at once.
  await page.click('#select-toggle');
  for (const name of ['Bramble', 'Clover', 'No Owner Ned']) await horseRow(page, name).click();
  check('3 horses selected', (await text(page, '#select-count')) === '3 horses selected', await text(page, '#select-count'));
  await page.click('#select-add');
  await page.locator('#picker-tiles .tile', { hasText: 'Flu/Tet booster' }).click();
  await page.waitForFunction(() => document.getElementById('unsent').textContent === '4 notes not sent');
  check('one item added to 3 horses in one step', true, await text(page, '#sync-message'));

  // ── The end-of-visit summary.
  await tab(page, 'visit');
  check('the summary lists 4 horses and the total', (await text(page, '#visit-total')) === '4 horses · Total £282.50', await text(page, '#visit-total'));
  // The practice's client is the yard: one bill, to the yard, which
  // invoices each owner. No split between owners on the vet's screen.
  check('the summary says the bill goes to the yard', (await text(page, '#visit-bill')) === `Bill to ${YARD_NAME}`, await text(page, '#visit-bill'));
  check('no split between owners when the yard is billed', (await page.locator('#visit-payers .pay').count()) === 0 && await page.locator('#visit-payers-title').isHidden());
  const owners = await page.locator('#visit-horses .horse', { hasText: 'Bramble' }).locator('small').last().textContent();
  check('each horse still names its owners, for the yard', owners === 'Owners: Jo Bloggs 50% · Ali Khan 50%', owners);
  const cutShort = await page.locator('#view-visit .amount').evaluateAll((nodes) => nodes.filter((n) => n.scrollWidth > n.clientWidth + 1).length);
  check('no amount of money is cut short on the summary', cutShort === 0, `${cutShort} cut`);
  const finishWidth = await page.locator('#finish').evaluate((node) => node.getBoundingClientRect().width / window.innerWidth);
  check('Finish visit is a full-width button', finishWidth > 0.9, `${Math.round(finishWidth * 100)}% of the screen`);
  await page.screenshot({ path: path.join(OUT, '3-visit.png'), fullPage: true });

  // ── Sync by hand with no signal: nothing is lost.
  await page.click('#unsent');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent.includes('No signal'));
  check('sync with no signal keeps every note', (await text(page, '#unsent')) === '4 notes not sent');

  // ── Close the app and open it again, still with no signal.
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  await tab(page, 'visit');
  check('the visit is still there after the app is opened again', (await text(page, '#visit-total')) === '4 horses · Total £282.50');

  // ── Finish the visit, still with no signal.
  await page.click('#finish');
  await page.waitForFunction(() => document.getElementById('visit-state').textContent.startsWith('Visit finished'));
  check('the visit is finished with no signal, and says it is not sent', (await text(page, '#visit-state')).includes('Not all sent yet'));

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
  check('a lost reply leaves the note marked not sent', (await text(page, '#unsent')) === '4 notes not sent', await text(page, '#unsent'));

  // ── Sync again. The first note goes a second time; the server must
  //    answer "duplicate". Then the finished visit goes.
  await page.click('#unsent');
  await page.waitForFunction(() => document.getElementById('unsent').textContent === 'All sent', null, { timeout: 30000 });
  check('everything is sent after the second sync, the finished visit too', true);
  check('the visit screen says all is sent', (await text(page, '#visit-state')).includes('All sent to the practice'), await text(page, '#visit-state'));
  // The server made the bill when the visit was finished: one bill, to
  // the yard. In test mode it has a TEST number.
  check('the visit screen shows the bill the server made', (await text(page, '#visit-state')).includes(`Bill ${BILL} to Palmer Farm, £282.50.`), await text(page, '#visit-state'));

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
    };
  });
  check('the practice holds 4 notes with 5 items', stored.count === 4 && stored.lines === 5, `${stored.count} notes, ${stored.lines} items`);
  check('the stored total is the total on the phone', stored.total === 282.5, `£${stored.total}`);
  check('the changed price was stored for that horse only', stored.willowLines === 'Flu/Tet booster 60.00, Sedation 27.50', stored.willowLines);
  check('the recheck date was stored', /^\d{4}-\d{2}-\d{2}$/.test(stored.willowRecheck || ''), stored.willowRecheck);
  check('the photo and the recording were stored and download complete', stored.willowFiles === 2 && stored.fileStatus === 200 && stored.fileBytes > 0);
  check(TEST_MODE ? 'the notes are marked as test notes' : 'the notes are real notes', stored.allTest === TEST_MODE);
  check(TEST_MODE ? 'the screen says the notes are test notes' : 'the screen does not say test notes', (await page.locator('#test-mode').isVisible()) === TEST_MODE);

  // ── History on the horse screen, and a new download shows the recheck.
  await tab(page, 'phone');
  await page.click('#download');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent === 'Horse list downloaded.');
  await tab(page, 'horses');
  await horseRow(page, 'Willow').click();
  check('the horse now shows its recheck as due', (await text(page, '#horse-due')).includes('Recheck due'), await text(page, '#horse-due'));
  check('the horse screen shows the note in its history', (await page.locator('#history .entry').count()) === 1);
  await page.click('#load-history');
  await page.waitForFunction(() => document.getElementById('history-message').textContent !== 'Loading…');
  check('older notes load without showing the same note twice', (await page.locator('#history .entry').count()) === 1);
  await page.click('#back');

  // ── The vet adds a price.
  await tab(page, 'prices');
  check('the booster is marked as a vaccination', (await page.locator('#prices .price', { hasText: 'Flu/Tet booster' }).textContent()).includes('records a vaccination'));
  await page.click('#price-add');
  check('a price can be marked as one of the yard\'s vaccinations', (await page.locator('#price-vaccination option').allTextContents()).join(',') === 'No,Flu/Tet');
  await page.fill('#price-name', 'Dental check');
  await page.fill('#price-amount', 'forty');
  await page.click('#price-save');
  check('a price that is not a number is refused, with the reason', (await text(page, '#price-message')).includes('as a number'));
  await page.fill('#price-amount', '48');
  await page.click('#price-save');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent === 'Dental check saved.');
  check('the vet adds a price', (await page.locator('#prices .price', { hasText: 'Dental check' }).count()) === 1);
  await page.screenshot({ path: path.join(OUT, '4-prices.png'), fullPage: true });

  // ── Compact and fast (section 5). Open the app again with no signal
  //    and measure it.
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  const speed = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#horses .horse')];
    const floor = document.querySelector('.tabs').getBoundingClientRect().top;
    const visible = rows.filter((row) => row.getBoundingClientRect().bottom <= floor).length;
    const small = [...document.querySelectorAll('#horses .horse, .tab, #select-toggle, #search')]
      .filter((node) => node.getBoundingClientRect().height < 44).length;
    return { ready: Math.round(performance.now()), visible, small, height: window.innerHeight };
  });
  check('opens with no signal in under 1 second', speed.ready < 1000, `${speed.ready} ms to the horse list`);
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
  check('page files are under 40 KB as the phone downloads them', files.length >= 3 && sent < 40 * 1024, `${Math.round(sent / 1024)} KB compressed`);
  check('and under 150 KB as written', written < 150 * 1024, `${Math.round(written / 1024)} KB`);
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
  for (const name of ['horses', 'visit', 'prices', 'phone']) {
    await tab(page, name);
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) wide.push(name);
  }
  await tab(page, 'horses');
  await horseRow(page, 'Thunderbolt Magnificent Champion of Wettenhall').click();
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) wide.push('horse');
  await page.click('#back');
  check('no sideways scroll on any screen of an iPhone 13', wide.length === 0, wide.join(','));
  check('no script errors', consoleErrors.length === 0, consoleErrors.join(' | ').slice(0, 300));

  // ── The practice's bills: a page, with a signal.
  await tab(page, 'phone');
  await Promise.all([page.waitForURL('**/c-vet/bills/'), page.click('a[href="/c-vet/bills/"]')]);
  check('the bills page lists the bill', (await page.locator('table').textContent()).includes(BILL));
  await page.click(`a:has-text("${BILL}")`);
  const billPage = await page.locator('body').textContent();
  check('the bill names the yard, each horse and the total', ['Palmer Farm', 'Willow', 'Bramble', 'No Owner Ned', '£282.50'].every((t) => billPage.includes(t)) && billPage.includes('Test bill') === TEST_MODE);
  check('the bill does not carry the clinical note', !billPage.includes('Mild heat'));
  await page.screenshot({ path: path.join(OUT, '6-bill.png'), fullPage: true });
  await page.goto(`${BASE}/c-vet/`);
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);

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
      const tx = db.transaction(['horses', 'entries', 'prices']);
      const counts = [];
      for (const name of ['horses', 'entries', 'prices']) {
        const count = tx.objectStore(name).count();
        count.onsuccess = () => { counts.push(count.result); if (counts.length === 3) resolve(counts.reduce((a, b) => a + b, 0)); };
      }
    };
    open.onerror = () => resolve(-1);
  }));
  check('sign out removes horses, notes and prices from the phone', after === 0, `${after} rows left`);
  check('sign out removes the stored page', stores.filter((name) => name.startsWith('cvet-')).length === 0, stores.join(','));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(results, null, 2));
  console.log(`\n${results.length - failed.length} of ${results.length} checks passed.`);
  console.log('The server must now hold: 4 clinical notes, 5 items (total 282.50), 2 files, 4 receipts (1 with attempts = 2), 1 finished visit, 1 bill (' + BILL + '), 4 prices. ' + (TEST_MODE
    ? 'Test mode: no vet record, charge or cost at the yard.'
    : 'At the yard: 4 vaccination records, 1 vet visit record (Willow: sedation and the recheck), charges for 3 horses (No Owner Ned has no owner), and 1 cost for the bill.'));
  process.exit(failed.length ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
