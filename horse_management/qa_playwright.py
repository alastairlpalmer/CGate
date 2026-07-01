"""Playwright QA driver. Run with venv active."""
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
SHOTS = "/tmp/claude-0/-home-user-CGate/110c7df2-e072-5b92-9f69-e833b64693fa/scratchpad"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

APP_URLS = [
    "/", "/finances/", "/horses/", "/horses/add/", "/owners/", "/owners/add/",
    "/locations/", "/placements/", "/invoicing/", "/invoicing/create/",
    "/invoicing/generate/", "/health/", "/billing/charges/", "/billing/costs/",
    "/billing/feed/", "/settings/",
]
# Staff-only actions a non-staff viewer must NOT reach
STAFF_URLS = [
    "/horses/add/", "/owners/add/", "/locations/add/", "/placements/add/",
    "/invoicing/create/", "/invoicing/generate/", "/billing/charges/add/",
]

results = {"auth_gate": [], "console": [], "access_control": [], "notes": []}

def login(page, user, pw):
    page.goto(f"{BASE}/accounts/login/")
    page.fill("input[name=username]", user)
    page.fill("input[name=password]", pw)
    page.click("button[type=submit], input[type=submit]")
    page.wait_for_load_state("networkidle")

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME)

    # --- 1. Auth gating (logged out) ---
    ctx = browser.new_context()
    page = ctx.new_page()
    for url in APP_URLS:
        r = page.goto(f"{BASE}{url}", wait_until="domcontentloaded")
        final = page.url
        gated = "/accounts/login/" in final
        results["auth_gate"].append({"url": url, "status": r.status, "redirected_to_login": gated, "final": final.replace(BASE,"")})
    ctx.close()

    # --- 2. Admin: visit pages, capture console errors + desktop screenshots ---
    ctx = browser.new_context(viewport={"width":1280,"height":900})
    page = ctx.new_page()
    console_msgs = []
    page.on("console", lambda m: console_msgs.append((m.type, m.text)) if m.type in ("error","warning") else None)
    page.on("pageerror", lambda e: console_msgs.append(("pageerror", str(e))))
    login(page, "admin", "AdminPass123!")
    for url in APP_URLS:
        console_msgs.clear()
        r = page.goto(f"{BASE}{url}", wait_until="networkidle")
        errs = [m for m in console_msgs if m[0] in ("error","pageerror")]
        results["console"].append({"url": url, "status": r.status, "console_errors": errs[:5]})
    # invoice detail + screenshot
    page.goto(f"{BASE}/invoicing/", wait_until="networkidle")
    page.screenshot(path=f"{SHOTS}/desktop_invoice_list.png", full_page=True)
    page.goto(f"{BASE}/invoicing/1/", wait_until="networkidle")
    page.screenshot(path=f"{SHOTS}/desktop_invoice_detail.png", full_page=True)
    page.goto(f"{BASE}/health/", wait_until="networkidle")
    page.screenshot(path=f"{SHOTS}/desktop_health.png", full_page=True)
    ctx.close()

    # --- 3. Mobile (375px) screenshots of table-heavy pages ---
    ctx = browser.new_context(viewport={"width":375,"height":812})
    page = ctx.new_page()
    login(page, "admin", "AdminPass123!")
    for name, url in [("dashboard","/"), ("horses","/horses/"), ("invoice_list","/invoicing/"),
                      ("invoice_detail","/invoicing/1/"), ("finances","/finances/"),
                      ("placements","/placements/"), ("health","/health/")]:
        page.goto(f"{BASE}{url}", wait_until="networkidle")
        # detect horizontal overflow
        overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
        sw = page.evaluate("() => document.documentElement.scrollWidth")
        results["notes"].append({"mobile_page": url, "h_overflow": overflow, "scrollWidth": sw})
        page.screenshot(path=f"{SHOTS}/mobile_{name}.png", full_page=True)
    ctx.close()

    # --- 4. Access control: viewer (non-staff) must not reach staff URLs ---
    ctx = browser.new_context()
    page = ctx.new_page()
    login(page, "viewer", "ViewPass123!")
    # confirm logged in
    r = page.goto(f"{BASE}/", wait_until="networkidle")
    logged_in = "/accounts/login/" not in page.url
    results["access_control"].append({"viewer_logged_in": logged_in, "dashboard_status": r.status})
    for url in STAFF_URLS:
        r = page.goto(f"{BASE}{url}", wait_until="domcontentloaded")
        results["access_control"].append({"url": url, "status": r.status,
            "blocked": r.status == 403 or "/accounts/login/" in page.url,
            "final": page.url.replace(BASE,"")})
    ctx.close()
    browser.close()

print(json.dumps(results, indent=2, default=str))
