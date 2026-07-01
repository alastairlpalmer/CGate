"""Refined mobile metrics excluding the Django Debug Toolbar."""
import json
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8000"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

PAGES = [("login","/accounts/login/"),("dashboard","/"),("horses_list","/horses/"),
    ("horse_detail","/horses/1/"),("horse_edit","/horses/1/edit/"),("new_arrival","/horses/new-arrival/"),
    ("owners_list","/owners/"),("owner_detail","/owners/1/"),("locations_list","/locations/"),
    ("location_detail","/locations/1/"),("placement_add","/placements/add/"),("invoice_list","/invoicing/"),
    ("invoice_detail","/invoicing/1/"),("invoice_create","/invoicing/create/?owner=1"),("invoice_generate","/invoicing/generate/"),
    ("health","/health/"),("vax_add","/health/vaccinations/add/"),("charges","/billing/charges/"),
    ("charge_add","/billing/charges/add/"),("costs","/billing/costs/"),("feed","/billing/feed/"),
    ("finances","/finances/"),("settings","/settings/")]

JS = r"""
() => {
  const inTB = el => el.closest && el.closest('#djDebug, [id^="djDebug"], #djDebugToolbar, .djdt-hidden, #djDebugToolbarHandle');
  const vw = window.innerWidth, vh = window.innerHeight;
  const small = [], seen = new Set();
  for (const el of document.querySelectorAll('a,button,input,select,textarea,[role=button]')) {
    if (inTB(el)) continue;
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    if (r.width===0||r.height===0||st.visibility==='hidden'||st.display==='none'||el.type==='hidden') continue;
    if (r.width<44 || r.height<44) {
      const txt=(el.textContent||el.getAttribute('aria-label')||el.name||'').trim().slice(0,28);
      const k=el.tagName+txt+Math.round(r.width)+'x'+Math.round(r.height);
      if(seen.has(k))continue; seen.add(k);
      small.push({tag:el.tagName.toLowerCase(),w:Math.round(r.width),h:Math.round(r.height),txt});
    }
  }
  const sf=[];
  for (const el of document.querySelectorAll('input,select,textarea')) {
    if (inTB(el)||el.type==='hidden'||el.type==='checkbox'||el.type==='radio') continue;
    const fs=parseFloat(getComputedStyle(el).fontSize);
    if(fs&&fs<16) sf.push({name:el.name||'',type:el.type||el.tagName.toLowerCase(),px:Math.round(fs)});
  }
  // bottom nav + clearance: does body content sit under the fixed bottom nav?
  let navH=0;
  for (const el of document.querySelectorAll('nav,footer,div')) {
    const st=getComputedStyle(el), r=el.getBoundingClientRect();
    if(st.position==='fixed'&&r.bottom>=vh-2&&r.height>0&&r.height<160&&r.width>vw*0.6){navH=Math.round(r.height);break;}
  }
  const bodyPB = parseFloat(getComputedStyle(document.body).paddingBottom)||0;
  const mainEl = document.querySelector('#main-content')||document.querySelector('main');
  const mainPB = mainEl? parseFloat(getComputedStyle(mainEl).paddingBottom)||0 : 0;
  return {smallTap:small.length, tapSample:small.slice(0,14), smallFontInputs:sf,
    navH, bodyPB:Math.round(bodyPB), mainPB:Math.round(mainPB)};
}
"""
out={}
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=CHROME)
    c=b.new_context(viewport={"width":390,"height":844},device_scale_factor=3,is_mobile=True,has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1")
    pg=c.new_page()
    pg.goto(f"{BASE}/accounts/login/"); pg.fill("input[name=username]","admin"); pg.fill("input[name=password]","AdminPass123!")
    pg.click("button[type=submit]"); pg.wait_for_load_state("networkidle")
    for name,url in PAGES:
        try:
            pg.goto(f"{BASE}{url}",wait_until="networkidle",timeout=20000)
            out[name]=pg.evaluate(JS)
        except Exception as e:
            out[name]={"error":str(e)[:80]}
    c.close(); b.close()
print(json.dumps(out,indent=1))
