"""Mobile-experience QA driver (iPhone-class emulation)."""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
SHOTS = "/tmp/claude-0/-home-user-CGate/110c7df2-e072-5b92-9f69-e833b64693fa/scratchpad/mob"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
import os; os.makedirs(SHOTS, exist_ok=True)

# invoice pks discovered at runtime
PAGES = [
    ("login", "/accounts/login/", False),
    ("dashboard", "/", True),
    ("horses_list", "/horses/", True),
    ("horse_detail", "/horses/1/", True),
    ("horse_edit", "/horses/1/edit/", True),
    ("new_arrival", "/horses/new-arrival/", True),
    ("owners_list", "/owners/", True),
    ("owner_detail", "/owners/1/", True),
    ("owner_edit", "/owners/1/edit/", True),
    ("locations_list", "/locations/", True),
    ("location_detail", "/locations/1/", True),
    ("placement_add", "/placements/add/", True),
    ("invoice_list", "/invoicing/", True),
    ("invoice_generate", "/invoicing/generate/", True),
    ("health", "/health/", True),
    ("health_vax", "/health/vaccinations/", True),
    ("vax_add", "/health/vaccinations/add/", True),
    ("charges", "/billing/charges/", True),
    ("charge_add", "/billing/charges/add/", True),
    ("costs", "/billing/costs/", True),
    ("feed", "/billing/feed/", True),
    ("finances", "/finances/", True),
    ("settings", "/settings/", True),
]

MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const vw = window.innerWidth;
  // horizontal overflow + offenders
  const hOverflow = de.scrollWidth > vw + 1;
  const offenders = [];
  if (hOverflow) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > vw + 2) {
        offenders.push({tag: el.tagName.toLowerCase(),
          cls: (el.className && el.className.toString().slice(0,60)) || '',
          right: Math.round(r.right), w: Math.round(r.width),
          txt: (el.textContent||'').trim().slice(0,40)});
        if (offenders.length >= 8) break;
      }
    }
  }
  // small tap targets (interactive, visible, < 44 in either dim)
  const small = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('a,button,input,select,textarea,[role=button],[onclick]')) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility==='hidden' || st.display==='none') continue;
    if (el.type === 'hidden') continue;
    if ((r.width < 44 || r.height < 44)) {
      const key = el.tagName+'|'+(el.textContent||el.getAttribute('aria-label')||el.name||'').trim().slice(0,25)+'|'+Math.round(r.width)+'x'+Math.round(r.height);
      if (seen.has(key)) continue; seen.add(key);
      small.push({tag: el.tagName.toLowerCase(), w: Math.round(r.width), h: Math.round(r.height),
        txt: (el.textContent||el.getAttribute('aria-label')||el.name||'').trim().slice(0,30)});
    }
  }
  // inputs: type/inputmode/font-size (iOS zooms if <16px)
  const inputs = [];
  for (const el of document.querySelectorAll('input,select,textarea')) {
    if (el.type === 'hidden') continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    inputs.push({name: el.name||'', type: el.type||el.tagName.toLowerCase(),
      inputmode: el.getAttribute('inputmode')||'', fontPx: Math.round(fs)});
  }
  const smallFontInputs = inputs.filter(i => i.fontPx && i.fontPx < 16);
  // fixed/sticky bottom bar height (mobile nav)
  let bottomBar = null;
  for (const el of document.querySelectorAll('nav,div,footer')) {
    const st = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if ((st.position==='fixed'||st.position==='sticky') && r.bottom >= window.innerHeight-2 && r.height>0 && r.height<160 && r.width > vw*0.6) {
      bottomBar = {tag: el.tagName.toLowerCase(), h: Math.round(r.height), cls:(el.className||'').toString().slice(0,40)};
      break;
    }
  }
  const viewportMeta = (document.querySelector('meta[name=viewport]')||{}).content || null;
  return {vw, scrollWidth: de.scrollWidth, hOverflow, offenders,
    smallTapTargets: small.length, tapSample: small.slice(0,12),
    inputCount: inputs.length, smallFontInputs, viewportMeta, bottomBar};
}
"""

def login(page):
    page.goto(f"{BASE}/accounts/login/")
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", "AdminPass123!")
    page.click("button[type=submit], input[type=submit]")
    page.wait_for_load_state("networkidle")

results = {}
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME)
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},   # iPhone 12/13/14 logical px
        device_scale_factor=3, is_mobile=True, has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    )
    page = ctx.new_page()
    console = []
    page.on("console", lambda m: console.append((m.type, m.text[:120])) if m.type in ("error",) else None)
    page.on("pageerror", lambda e: console.append(("pageerror", str(e)[:120])))

    # discover the big invoice pk (most line items) via list page after login
    login(page)
    for name, url, _auth in PAGES:
        console.clear()
        try:
            r = page.goto(f"{BASE}{url}", wait_until="networkidle", timeout=20000)
            status = r.status
        except Exception as e:
            results[name] = {"error": str(e)[:120]}
            continue
        try:
            m = page.evaluate(MEASURE_JS)
        except Exception as e:
            m = {"measure_error": str(e)[:120]}
        m["status"] = status
        m["console_errors"] = console[:4]
        results[name] = m
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)

    # invoice detail (big, 12 items) + invoice create with preview
    for name, url in [("invoice_detail_big", "/invoicing/1/"), ("invoice_create", "/invoicing/create/?owner=1")]:
        console.clear()
        r = page.goto(f"{BASE}{url}", wait_until="networkidle")
        m = page.evaluate(MEASURE_JS); m["status"]=r.status; m["console_errors"]=console[:4]
        results[name] = m
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)

    # Interaction: open mobile "More" sheet / nav on dashboard (above the fold)
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.screenshot(path=f"{SHOTS}/dashboard_fold.png")  # above-the-fold only

    ctx.close(); browser.close()

print(json.dumps(results, indent=1, default=str))
