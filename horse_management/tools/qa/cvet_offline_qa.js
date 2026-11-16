// Offline check of the C-vet prototype (docs/C_VET_PLAN.md, phase 1).
//
// Drives the test page in Chromium the way the phone test does: download
// the horse list, lose the signal, open the app again, save notes with a
// photo and a voice recording, get the signal back, sync. It also cuts
// the reply to one upload, to prove that the note is then stored once.
//
//   # on a farrier_seed.py + cvet_seed.py database (see README.md), with the page on:
//   CVET_PROTOTYPE_ENABLED=True python manage.py runserver 127.0.0.1:8765
//   node tools/qa/cvet_offline_qa.js http://127.0.0.1:8765 /path/to/out
//
// Then count the rows: the script prints how many the server must hold.
//
// This is Chromium on a computer. It does not replace the test on a real
// iPhone, which is the point of phase 1: Safari has its own storage and
// service worker rules.

const fs = require('fs');
const path = require('path');
const { chromium, devices } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8765';
const OUT = process.argv[3] || 'cvet_qa';
const PASSWORD = 'qa-pass-123';

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

async function addNote(page, horseIndex, note, { photo = false, voice = false } = {}) {
  await page.locator('#horses .horse').nth(horseIndex).click();
  await page.fill('#note', note);
  if (photo) {
    await page.setInputFiles('#photo', { name: 'leg.png', mimeType: 'image/png', buffer: PHOTO });
    await page.waitForFunction(() => document.getElementById('photo-state').textContent.startsWith('Photo ready'));
  }
  if (voice) {
    await page.click('#record');
    await page.waitForFunction(() => document.getElementById('record').textContent === 'Stop recording');
    await page.waitForTimeout(1200);
    await page.click('#record');
    await page.waitForFunction(() => document.getElementById('audio-state').textContent.startsWith('Voice recorded'));
  }
  await page.click('#save');
  await page.waitForFunction(() => document.getElementById('entry').hidden);
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

  // Sign in, as the vet does once with a signal. No "next" address: a
  // Vet-role login must land on C-vet by itself.
  await page.goto(`${BASE}/accounts/login/`);
  await page.fill('input[name=username]', 'vet');
  await page.fill('input[name=password]', PASSWORD);
  await Promise.all([page.waitForURL('**/c-vet/'), page.click('button[type=submit]')]);
  check('a vet login lands on C-vet', new URL(page.url()).pathname === '/c-vet/');
  await page.goto(`${BASE}/health/`);
  check('a vet login cannot open a yard page', new URL(page.url()).pathname === '/c-vet/', page.url());

  // 1. The page stores itself.
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, null, { timeout: 15000 });
  check('service worker controls the page', true);

  // 2. Download the horse list.
  await page.click('#download');
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  const horses = await page.locator('#horses .horse').count();
  check('horse list downloaded', horses > 0, `${horses} horses`);
  await page.screenshot({ path: path.join(OUT, '1-online.png'), fullPage: true });

  // 3. Lose the signal and open the app again.
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  check('page opens with no signal', (await text(page, 'h1')).startsWith('C-vet'));
  check('horse list is still there with no signal', (await page.locator('#horses .horse').count()) === horses);
  check('signal chip says No signal', (await text(page, '#signal')) === 'No signal');

  // 4. Search works with no signal.
  const firstName = (await page.locator('#horses .horse .strong').first().textContent()).trim();
  await page.fill('#search', firstName);
  const found = await page.locator('#horses .horse').count();
  check('search works with no signal', found >= 1 && found <= horses, `${found} match "${firstName}"`);
  await page.fill('#search', '');

  // 5. Save three notes with no signal.
  await addNote(page, 0, 'Mild heat left fore, no lameness at trot. Recheck in two weeks.', { photo: true, voice: true });
  await addNote(page, 1, 'Flu and tetanus booster given.');
  await addNote(page, 2, '', { photo: true });
  check('counter shows 3 notes not sent', (await text(page, '#unsent')) === '3 notes not sent', await text(page, '#unsent'));
  await page.screenshot({ path: path.join(OUT, '2-offline-with-notes.png'), fullPage: true });

  // 6. Sync by hand with no signal: nothing is lost.
  await page.click('#sync');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent.includes('No signal'));
  check('sync with no signal keeps every note', (await text(page, '#unsent')) === '3 notes not sent');

  // 7. Close the app and open it again, still with no signal.
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#entries .entry').length === 3);
  check('notes are still there after the app is opened again', true);

  // 8. The signal is back, but the reply to the first upload is lost:
  //    the server stores the note and the phone never hears about it.
  let cut = 0;
  await context.route('**/c-vet/api/entries/', async (route) => {
    if (cut === 0) {
      cut += 1;
      await route.fetch();     // the server stores the note...
      await route.abort();     // ...and the reply never arrives
      return;
    }
    await route.continue();
  });
  //    Nobody taps Sync here: the page sends by itself when the signal
  //    comes back.
  await context.setOffline(false);
  for (let i = 0; i < 100 && cut === 0; i += 1) await page.waitForTimeout(100);
  check('the page sends by itself when the signal is back', cut === 1);
  await page.waitForFunction(() => !document.getElementById('sync').disabled);
  check('a lost reply leaves the note marked not sent', (await text(page, '#unsent')) === '3 notes not sent', await text(page, '#unsent'));

  // 9. Sync again. The first note goes a second time; the server must
  //    answer "duplicate" and all three must end as sent.
  await page.click('#sync');
  await page.waitForFunction(() => document.getElementById('unsent').textContent === 'Everything is sent', null, { timeout: 30000 });
  check('everything is sent after the second sync', true);
  const tries = await page.locator('#entries .entry', { hasText: '2 tries' }).count();
  check('one note needed 2 tries', tries === 1, `${tries}`);
  await page.screenshot({ path: path.join(OUT, '3-synced.png'), fullPage: true });

  // 10. Sync once more: nothing goes again.
  let extra = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/c-vet/api/entries/')) extra += 1;
  });
  await page.click('#sync');
  await page.waitForFunction(() => document.getElementById('sync-message').textContent === 'Nothing to send.');
  check('a third sync sends nothing', extra === 0, `${extra} uploads`);

  // 11. Compact and fast (docs/C_VET_PLAN.md, section 5). Open the app
  //     again with no signal and measure it.
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  const speed = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#horses .horse')];
    const visible = rows.filter((row) => row.getBoundingClientRect().bottom <= window.innerHeight).length;
    const small = rows.filter((row) => row.getBoundingClientRect().height < 44).length;
    return { ready: Math.round(performance.now()), visible, small, height: window.innerHeight };
  });
  check('opens with no signal in under 1 second', speed.ready < 1000, `${speed.ready} ms to the horse list`);
  check('8 or more horses on the first screen', speed.visible >= 8, `${speed.visible} on a ${speed.height} px screen`);
  check('every horse row is 44 px or more to tap', speed.small === 0, `${speed.small} too small`);
  const weight = await page.evaluate(() => performance.getEntriesByType('resource')
    .filter((entry) => entry.name.includes('/static/cvet/'))
    .reduce((sum, entry) => sum + (entry.decodedBodySize || 0), 0));
  check('page files are under 100 KB', weight > 0 && weight < 100 * 1024, `${Math.round(weight / 1024)} KB`);
  await page.screenshot({ path: path.join(OUT, '4-compact.png') });
  await context.setOffline(false);

  // 12. The page fits a phone.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check('no sideways scroll on an iPhone 13', !overflow);
  check('no script errors', consoleErrors.length === 0, consoleErrors.join(' | ').slice(0, 300));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(results, null, 2));
  console.log(`\n${results.length - failed.length} of ${results.length} checks passed.`);
  console.log('The server must now hold exactly 3 entries: 2 with a photo, 1 with a recording, and 1 with attempts = 2.');
  process.exit(failed.length ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
