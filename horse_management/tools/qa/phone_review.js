// The phone review of the main app (docs/MOBILE_APP_PLAN.md).
//
//   node tools/qa/phone_review.js http://127.0.0.1:8765 /tmp/phone-qa [scale]
//
// It signs in as the seed_qa administrator on a 390 x 844 touch screen.
// Part 1 measures each page: its height in screens, the fixed bars, the
// share of small text, and takes a screenshot of the first screen.
// Part 2 runs the behaviour checks of the plan (items A1 to A7 and B1)
// and prints PASS or FAIL for each. It is a report: it always exits 0.
// It is Chromium, not an iPhone: the test on real phones is section 8
// of the plan. `scale` is the screenshot scale (1; use 2 for a person).
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8765';
const OUT = process.argv[3] || '/tmp/phone-qa';
const SCALE = Number(process.argv[4] || 1);
const PAGES = [
    ['dashboard', '/'], ['horses', '/horses/'], ['locations', '/locations/'], ['health', '/health/'],
    ['vaccinations', '/health/vaccinations/'], ['farrier', '/health/farrier/'], ['owners', '/owners/'],
    ['invoices', '/invoicing/'], ['settings', '/settings/'],
];
const log = (...a) => console.log(...a);

async function signIn(context) {
    const page = await context.newPage();
    await page.goto(BASE + '/accounts/login/');
    await page.fill('input[name=username]', 'admin');
    await page.fill('input[name=password]', 'AdminPass123!');
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
    return page;
}

// Resolves when htmx has put a response in the page (or after 15 seconds).
const settled = (page) => page.evaluate(() => new Promise((done) => {
    document.body.addEventListener('htmx:afterSettle', () => done(), { once: true });
    setTimeout(done, 15000);
}));

const measure = () => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const bars = [];
    document.querySelectorAll('body *').forEach((el) => {
        const s = getComputedStyle(el);
        if (s.position !== 'fixed' && s.position !== 'sticky') return;
        const r = el.getBoundingClientRect();
        if (r.width > vw * 0.6 && r.height > 20 && r.height < vh * 0.4 && r.top < vh && r.bottom > 0 && s.opacity !== '0') bars.push(r);
    });
    const top = bars.filter((r) => r.top < vh / 2).reduce((max, r) => Math.max(max, r.bottom), 0);
    const bottom = bars.filter((r) => r.top >= vh / 2).reduce((min, r) => Math.min(min, r.top), vh);
    const main = document.getElementById('main-content');
    let small = 0, total = 0;
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent.trim();
        const box = node.parentElement.getBoundingClientRect();
        if (!text || !box.width || !box.height) continue;
        total += text.length;
        if (parseFloat(getComputedStyle(node.parentElement).fontSize) <= 12) small += text.length;
    }
    const rows = [...document.querySelectorAll('[data-horse-row]')].filter((row) => {
        const r = row.getBoundingClientRect();
        return r.height > 0 && r.top >= top && r.bottom <= bottom;
    }).length;
    return {
        screens: +(document.documentElement.scrollHeight / vh).toFixed(1),
        sideways: document.documentElement.scrollWidth > vw,
        bars: Math.round(top + (vh - bottom)),
        smallText: total ? Math.round((small / total) * 100) : 0,
        elements: document.querySelectorAll('*').length,
        horseRows: rows,
    };
};

(async () => {
    const browser = await chromium.launch();
    setTimeout(() => { log('Stopped: over 5 minutes.'); process.exit(0); }, 300000);
    const phone = { viewport: { width: 390, height: 844 }, deviceScaleFactor: SCALE, isMobile: true, hasTouch: true };
    const context = await browser.newContext(phone);
    let page = await signIn(context);
    const results = [];
    const check = async (id, what, fn) => {
        let pass = false, note = '';
        try { [pass, note] = await fn(); } catch (err) { note = 'error: ' + err.message.split('\n')[0].slice(0, 120); }
        results.push(`${pass ? 'PASS' : 'FAIL'}  ${id.padEnd(3)} ${what}${note ? ' (' + note + ')' : ''}`);
    };

    // ── Part 1: each page ──
    log('page'.padEnd(14), 'screens', 'bars px', '12px text', 'elements', 'html KB', 'horses on screen 1');
    for (const [name, url] of PAGES) {
        await page.goto(BASE + url, { waitUntil: 'networkidle' });
        const m = await page.evaluate(measure);
        const html = await page.evaluate(() => performance.getEntriesByType('navigation')[0].decodedBodySize);
        log(name.padEnd(14), String(m.screens).padEnd(7), String(m.bars).padEnd(7), (m.smallText + '%').padEnd(9),
            String(m.elements).padEnd(8), String(Math.round(html / 1000)).padEnd(7), name === 'horses' ? m.horseRows : '',
            m.sideways ? 'SIDEWAYS SCROLL' : '');
        await page.screenshot({ path: `${OUT}/${name}.png` });
    }
    const horseUrl = await page.evaluate(async () => {
        const html = await (await fetch('/horses/')).text();
        const found = html.match(/href="(\/horses\/\d+\/)[^"]*"/);
        return found ? found[1] : '/horses/1/';
    });
    await page.goto(BASE + horseUrl, { waitUntil: 'networkidle' });
    await page.screenshot({ path: `${OUT}/horse.png` });
    log('horse page'.padEnd(14), (await page.evaluate(measure)).screens, 'screens');

    // ── Part 2: behaviour ──
    const tab = (href) => `#mobile-tab-bar a[href="${href}"]`;
    const message = () => page.evaluate(() => {
        const toast = document.getElementById('toast-container');
        const banner = document.querySelector('[data-offline-banner], [data-request-error]');
        return ((toast ? toast.innerText : '') + (banner ? banner.innerText : '')).trim();
    });

    await check('B1', 'A tap on a tab downloads under 15 KB', async () => {
        await page.goto(BASE + '/horses/', { waitUntil: 'networkidle' });
        let bytes = 0;
        // Pages only: a script or a map tile that loads once is not part of a tap.
        const count = (response) => {
            const headers = response.headers();
            if ((headers['content-type'] || '').includes('text/html')) bytes += Number(headers['content-length'] || 0);
        };
        page.on('response', count);
        const done = settled(page);
        await page.tap(tab('/'));
        await done;
        await page.waitForTimeout(300);
        page.off('response', count);
        return [bytes > 0 && bytes < 15000, Math.round(bytes / 1000) + ' KB'];
    });

    await check('A2', 'A tap with no signal shows a message', async () => {
        await page.goto(BASE + '/', { waitUntil: 'networkidle' });
        await context.setOffline(true);
        await page.tap(tab('/horses/'));
        await page.waitForTimeout(2500);
        const text = await message();
        await page.screenshot({ path: `${OUT}/no_signal_tap.png` });
        await context.setOffline(false);
        return [text.length > 0, text.slice(0, 60) || 'nothing on the screen'];
    });

    await check('A2', 'A server error on a tap shows a message', async () => {
        await page.goto(BASE + '/', { waitUntil: 'networkidle' });
        await page.route('**/horses/', (route) => route.fulfill({ status: 500, contentType: 'text/html', body: '<h1>Server Error (500)</h1>' }));
        await page.tap(tab('/horses/'));
        await page.waitForTimeout(1500);
        const text = await message();
        await page.unroute('**/horses/');
        return [text.length > 0, text.slice(0, 60) || 'nothing on the screen'];
    });

    await check('A3', 'A save with no signal keeps the form', async () => {
        await page.goto(BASE + horseUrl, { waitUntil: 'networkidle' });
        // The vaccination form has its date filled in, so Save sends it.
        await page.tap('#main-content a.qa-tile[href*="/health/vaccinations/add/"] >> visible=true');
        await page.waitForSelector('#popup-body form', { timeout: 5000 });
        await context.setOffline(true);
        await page.tap('#popup-panel button[type=submit] >> nth=0');
        await page.waitForTimeout(2500);
        const kept = await page.evaluate(() => !!document.querySelector('#popup-body form'));
        await page.screenshot({ path: `${OUT}/no_signal_save.png` });
        await context.setOffline(false);
        return [kept, kept ? '' : 'the form was replaced by the error text'];
    });

    await check('A7', 'Back closes the notification drawer and stays on the page', async () => {
        await page.goto(BASE + '/horses/', { waitUntil: 'networkidle' });
        await page.goto(BASE + '/health/', { waitUntil: 'networkidle' });
        await page.tap('.phone-bell');
        await page.waitForTimeout(700);
        await page.goBack();
        await page.waitForTimeout(900);
        const path = new URL(page.url()).pathname;
        return [path === '/health/', 'now on ' + path];
    });

    await check('A7', 'Back closes the horse preview and stays on the list', async () => {
        await page.goto(BASE + '/', { waitUntil: 'networkidle' });
        await page.goto(BASE + '/horses/', { waitUntil: 'networkidle' });
        await page.tap('[data-horse-row] a[href^="/horses/"] >> nth=0');
        await page.waitForTimeout(1200);
        await page.goBack();
        await page.waitForTimeout(900);
        const path = new URL(page.url()).pathname;
        return [path === '/horses/', 'now on ' + path];
    });

    await check('A6', 'No page copies stay on the phone after sign-out', async () => {
        await page.goto(BASE + '/owners/', { waitUntil: 'networkidle' });
        const done = settled(page);
        await page.tap(tab('/horses/'));
        await done;
        await Promise.all([
            page.waitForNavigation({ timeout: 10000 }),
            page.evaluate(() => { setTimeout(() => document.querySelector('form[action*="logout"]').submit(), 50); }),
        ]);
        const bytes = await page.evaluate(() => (localStorage.getItem('htmx-history-cache') || '').length);
        return [bytes === 0, Math.round(bytes / 1000) + ' KB left in localStorage'];
    });

    await check('A1', 'A tap after the sign-in has ended shows the sign-in page', async () => {
        await page.close();
        page = await signIn(context);
        await page.goto(BASE + '/', { waitUntil: 'networkidle' });
        await context.clearCookies();
        await page.tap(tab('/horses/'));
        await page.waitForTimeout(2000);
        const form = await page.evaluate(() => !!document.querySelector('input[name=password]'));
        await page.screenshot({ path: `${OUT}/sign_in_ended.png` });
        return [form, form ? '' : 'an empty page'];
    });

    log('');
    results.forEach((line) => log(line));
    await browser.close();
    process.exit(0);
})();
