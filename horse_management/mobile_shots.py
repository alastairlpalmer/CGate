"""Clean mobile screenshots (Django Debug Toolbar hidden)."""
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8000"; CHROME="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
S="/tmp/claude-0/-home-user-CGate/110c7df2-e072-5b92-9f69-e833b64693fa/scratchpad/mob"
HIDE="#djDebug,#djDebugToolbar,#djDebugToolbarHandle,[id^=djDebug]{display:none!important}"
PAGES=[("dashboard","/",True),("horses_list","/horses/",True),("horse_detail","/horses/1/",False),
 ("horse_edit","/horses/1/edit/",True),("new_arrival","/horses/new-arrival/",True),
 ("owner_detail","/owners/1/",True),("location_detail","/locations/1/",True),
 ("invoice_list","/invoicing/",True),("invoice_detail","/invoicing/1/",True),
 ("invoice_create","/invoicing/create/?owner=1",True),("health","/health/",True),
 ("charges","/billing/charges/",True),("costs","/billing/costs/",True),("finances","/finances/",True),
 ("placement_add","/placements/add/",True),("settings","/settings/",True),("vax_add","/health/vaccinations/add/",True)]
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=CHROME)
    c=b.new_context(viewport={"width":390,"height":844},device_scale_factor=2,is_mobile=True,has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1")
    pg=c.new_page()
    pg.goto(f"{BASE}/accounts/login/"); pg.fill("input[name=username]","admin"); pg.fill("input[name=password]","AdminPass123!")
    pg.click("button[type=submit]"); pg.wait_for_load_state("networkidle")
    for name,url,full in PAGES:
        pg.goto(f"{BASE}{url}",wait_until="networkidle")
        pg.add_style_tag(content=HIDE)
        pg.screenshot(path=f"{S}/c_{name}.png", full_page=full)
        if full:
            pg.screenshot(path=f"{S}/f_{name}.png")  # above the fold too
    b.close()
print("done")
