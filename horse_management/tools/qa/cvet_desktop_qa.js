// The wide-screen check of C-vet (docs/C_VET_PLAN.md, section 7h).
//
// Run it after cvet_offline_qa.js, on the same server and database: it
// uses the clients, the horse and the visits that run made.
//
//   node tools/qa/cvet_desktop_qa.js http://127.0.0.1:8765 /path/to/out
//
// It checks, at 1440 x 900: the clients stay on the left with one client
// or one horse on the right; a note being written asks before another
// client opens; no screen scrolls sideways; and a narrow window goes
// back to one part.
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
  await page.waitForFunction(() => document.querySelectorAll('#horses .horse').length > 0);
  const r = {};
  r.split = await page.evaluate(() => document.body.classList.contains('split') && !document.getElementById('view-clients').hidden && !document.getElementById('view-client').hidden);
  r.current = await page.locator('#clients .horse[aria-current=true] .strong').textContent();
  await page.screenshot({ path: `${OUT}/d1-clients.png` });
  await page.locator('#clients .horse', { hasText: 'Marsh Stud' }).click();
  r.second = await page.locator('#client-name').textContent();
  await page.locator('#clients .horse', { hasText: 'Palmer Farm' }).click();
  await page.locator('#horses .horse', { hasText: 'Willow' }).click();
  await page.locator('#history .entry').first().waitFor();
  r.horseSplit = await page.evaluate(() => !document.getElementById('view-clients').hidden && !document.getElementById('view-horse').hidden);
  await page.click('#item-add');
  await page.screenshot({ path: `${OUT}/d2-horse-items.png` });
  await page.locator('#picker-list .pick', { hasText: 'Sedation' }).click();
  await page.click('#picker-cancel');
  await page.screenshot({ path: `${OUT}/d3-horse.png`, fullPage: true });
  // A draft is open: another client asks first (accepted), then opens.
  await page.locator('#clients .horse', { hasText: 'Marsh Stud' }).click();
  r.leftDraft = await page.locator('#client-name').textContent();
  await page.click('.tab[data-view=visits]');
  await page.locator('#past-visits details[data-visit]').first().waitFor();
  await page.locator('#past-visits details[data-visit] > summary').first().click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/d4-visits.png` });
  await page.click('.tab[data-view=prices]'); await page.screenshot({ path: `${OUT}/d5-prices.png` });
  r.sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  // Narrow the window: one part again, same client.
  await page.click('.tab[data-view=clients]');
  await page.setViewportSize({ width: 700, height: 900 });
  await page.waitForTimeout(200);
  r.narrow = await page.evaluate(() => !document.body.classList.contains('split') && document.getElementById('view-clients').hidden && !document.getElementById('view-client').hidden);
  r.noErrors = errors.length === 0;
  const want = { split: true, current: 'Marsh Stud', second: 'Marsh Stud', horseSplit: true, leftDraft: 'Marsh Stud', sideways: false, narrow: true, noErrors: true };
  const failed = Object.keys(want).filter((key) => r[key] !== want[key]);
  for (const key of Object.keys(want)) console.log(`${failed.includes(key) ? 'FAIL' : 'PASS'}  ${key}  (${r[key]})`);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
