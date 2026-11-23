// The three pictures that static/manifest.json names (docs/MOBILE_APP_PLAN.md,
// item D3). Run against the yard of tools/qa/app_shots_seed.py: the steps
// are at the top of that file.
//
//   node tools/qa/app_shots.js http://127.0.0.1:8770 static/screenshots
//
// Each picture is a phone screen of 390 x 844 at double size: 780 x 1688,
// the size the manifest gives. JPEG keeps each under about 150 KB.
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8770';
const OUT = process.argv[3] || 'static/screenshots';

(async () => {
    const browser = await chromium.launch();
    setTimeout(() => { console.log('Stopped: over 2 minutes.'); process.exit(1); }, 120000);
    const context = await browser.newContext({
        viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
        // No service worker: a stored copy of an old stylesheet must not be in a picture.
        serviceWorkers: 'block',
    });
    const page = await context.newPage();
    await page.goto(BASE + '/accounts/login/');
    await page.fill('input[name=username]', 'sam');
    await page.fill('input[name=password]', 'ShotsPass123!');
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

    const horse = async () => {
        await page.goto(BASE + '/horses/', { waitUntil: 'networkidle' });
        return page.getAttribute('tr[data-horse-row] a[data-horse-link]', 'href');
    };
    const shots = [['dashboard', '/'], ['horses', '/horses/'], ['horse', await horse()]];
    for (const [name, url] of shots) {
        await page.goto(BASE + url, { waitUntil: 'networkidle' });
        // The install card is for a browser; it is not part of the app's look.
        await page.evaluate(() => { const card = document.querySelector('[data-install-card]'); if (card) card.hidden = true; });
        await page.waitForTimeout(600);
        await page.screenshot({ path: `${OUT}/${name}.jpg`, type: 'jpeg', quality: 82 });
        console.log(`${OUT}/${name}.jpg`);
    }
    await browser.close();
    process.exit(0);
})();
