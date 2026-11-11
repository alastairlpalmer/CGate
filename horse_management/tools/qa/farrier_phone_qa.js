// Phone check of the farrier page (docs/FARRIER_PAGE_PLAN.md, step 7).
//
// Drives the whole farrier flow at two phone sizes and reports, per screen:
// horizontal overflow, tap targets under 44 px, and a screenshot.
//
//   python manage.py runserver 127.0.0.1:8765     # on a farrier_seed.py database
//   node tools/qa/farrier_phone_qa.js http://127.0.0.1:8765 /path/to/out
//
// This is a simulated phone. It does not replace a check on a real phone
// at the yard (weak signal, sunlight, gloves).

const fs = require('fs');
const path = require('path');
const { chromium, devices } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8765';
const OUT = process.argv[3] || 'farrier_qa';
const PASSWORD = 'qa-pass-123';
const MIN_TAP = 44;
const DEVICES = ['iPhone SE', 'iPhone 13'];

function measure(minTap) {
  const vw = window.innerWidth;
  const overflow = document.documentElement.scrollWidth > vw + 1;
  const wide = [];
  if (overflow) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width && r.right > vw + 2) {
        wide.push(`${el.tagName.toLowerCase()} "${(el.textContent || '').trim().slice(0, 30)}" right=${Math.round(r.right)}`);
        if (wide.length > 5) break;
      }
    }
  }
  const small = [];
  for (const el of document.querySelectorAll('a, button, summary, input:not([type=hidden]), select, textarea')) {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (!r.width || style.visibility === 'hidden' || style.display === 'none') continue;
    if (r.height < minTap || r.width < minTap) {
      const label = (el.textContent || el.value || el.name || el.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 40);
      small.push(`${el.tagName.toLowerCase()} "${label}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  }
  return { overflow, wide, small: [...new Set(small)] };
}

async function runDevice(browser, deviceName, report) {
  const context = await browser.newContext({ ...devices[deviceName] });
  const page = await context.newPage();
  const shots = path.join(OUT, deviceName.replace(/ /g, '-').toLowerCase());
  fs.mkdirSync(shots, { recursive: true });
  const results = (report[deviceName] = {});
  let n = 0;

  async function check(name, fullPage = true) {
    await page.waitForLoadState('networkidle');
    results[name] = await page.evaluate(measure, MIN_TAP);
    n += 1;
    await page.screenshot({ path: path.join(shots, `${String(n).padStart(2, '0')}-${name}.png`), fullPage });
  }

  // A horse shod on an earlier run is no longer due, so it sits in the
  // folded "Other horses" list. Open the list first when needed.
  async function openHorse(horse) {
    const row = page.locator(`#horse-list a:has-text("${horse}")`);
    if (!(await row.isVisible())) await page.click('#horse-list summary');
    await row.click();
  }

  async function tapPrice(horse, price) {
    await openHorse(horse);
    await page.waitForSelector(`#horse-list button[name=item]:has-text("${price}")`);
    await page.click(`#horse-list button[name=item]:has-text("${price}")`);
    await page.waitForTimeout(500);
  }

  // 1. The QR code holds /farrier/. Signed out, it asks for a login.
  await page.goto(`${BASE}/farrier/`);
  await check('sign-in');
  await page.fill('input[name=username]', 'farrier');
  await page.fill('input[name=password]', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForURL('**/farrier/');
  await check('today');

  // 2. Search.
  await page.fill('input[name=q]', 'wil');
  await page.waitForTimeout(800);
  await check('today-search');
  await page.fill('input[name=q]', '');
  await page.waitForTimeout(800);

  // 3. Tap a horse, tap a price. One with a typed price, a long name.
  await openHorse('Echo');
  await page.waitForSelector('#horse-list button[name=item]');
  await check('horse-panel', false);
  await page.click('#horse-list button[name=item]:has-text("Full set")');
  await page.waitForTimeout(500);
  await openHorse('Fudge');
  await page.waitForSelector('#horse-list input[name=price]');
  await page.fill('#horse-list input[name=price]', '42.50');
  await page.click('#horse-list button[name=item]:has-text("Trim")');
  await page.waitForTimeout(500);
  await tapPrice('Thunderbolt', 'Trim');
  await tapPrice('Willow', 'Front shoes');
  await check('today-with-lines');

  // 4. Review, then Finish visit.
  await page.click('#review-bar a');
  await check('review');
  const finish = page.locator('button:has-text("Finish")');
  results.finish_button = await finish.count();
  if (results.finish_button) {
    await finish.first().click();
    await check('finished');
  }

  // 5. My visits, a visit page, Change.
  await page.goto(`${BASE}/farrier/visits/`);
  await check('my-visits');
  await page.locator('main a[href*="/farrier/visits/"]').first().click();
  await check('visit-detail');
  const change = page.locator('main a[href*="/lines/"]');
  if (await change.count()) {
    await change.first().click();
    await check('change-line');
  }

  // 6. My prices, Add a price.
  await page.goto(`${BASE}/farrier/prices/`);
  await check('my-prices');
  await page.goto(`${BASE}/farrier/prices/add/`);
  await check('add-price');

  // 7. The fence: a staff page sends the farrier back.
  await page.goto(`${BASE}/owners/`);
  results.fence = { landed_on: page.url().replace(BASE, "") };

  await context.close();
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const report = {};
  try {
    for (const name of DEVICES) await runDevice(browser, name, report);
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  }
})();
