// The marketing pages' only script (templates/marketing/base.html):
// the header goes solid on scroll, and the phone menu opens and closes.
(function () {
    'use strict';
    var header = document.querySelector('[data-site-header]');
    if (!header) { return; }
    var overHero = header.hasAttribute('data-over-hero');
    var solid = (header.getAttribute('data-solid-class') || '').split(' ').filter(Boolean);
    var clear = (header.getAttribute('data-clear-class') || '').split(' ').filter(Boolean);
    var button = header.querySelector('[data-menu-button]');
    var menu = document.getElementById('site-menu');
    var open = false;

    function paint() {
        var isSolid = !overHero || open || window.scrollY > 24;
        header.classList.remove.apply(header.classList, isSolid ? clear : solid);
        header.classList.add.apply(header.classList, isSolid ? solid : clear);
    }

    function setOpen(value) {
        open = value;
        if (menu) { menu.hidden = !open; }
        if (button) { button.setAttribute('aria-expanded', open ? 'true' : 'false'); }
        paint();
    }

    if (button) { button.addEventListener('click', function () { setOpen(!open); }); }
    document.addEventListener('keydown', function (e) {
        if (open && e.key === 'Escape') { setOpen(false); if (button) { button.focus(); } }
    });
    window.addEventListener('resize', function () { if (open && window.innerWidth >= 768) { setOpen(false); } });
    window.addEventListener('scroll', paint, { passive: true });
    paint();
})();

// Scroll reveal: .mk-reveal gets .is-visible once it comes into view.
// Without IntersectionObserver, or with less motion asked for, all show at once.
(function () {
    'use strict';
    var items = document.querySelectorAll('.mk-reveal');
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!('IntersectionObserver' in window) || still) {
        for (var i = 0; i < items.length; i++) { items[i].classList.add('is-visible'); }
        return;
    }
    var seen = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) { entry.target.classList.add('is-visible'); seen.unobserve(entry.target); }
        });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    for (var j = 0; j < items.length; j++) { seen.observe(items[j]); }
})();

// Pricing calculator (templates/marketing/pricing.html). The same sum as
// marketing.content.quote(): (plan price + ticked add-ons) per horse,
// times the horses; an add-on that is not out yet gives the larger offer.
(function () {
    'use strict';
    var form = document.querySelector('[data-calculator]');
    if (!form) { return; }
    var number = form.querySelector('[data-horses]');
    var slider = form.querySelector('[data-horse-slider]');
    var plans = form.querySelectorAll('[data-plan]');
    var addons = form.querySelectorAll('[data-addon]');
    var months = Number(form.getAttribute('data-months'));
    var standard = Number(form.getAttribute('data-offer'));
    var partner = Number(form.getAttribute('data-partner'));
    var large = Number(slider.max);
    var most = Number(number.max) || 100000;

    function money(value) {
        var pence = Math.round(value * 100);
        var whole = pence % 100 === 0;
        return '£' + (pence / 100).toLocaleString('en-GB', { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 });
    }
    function put(name, text) {
        var els = document.querySelectorAll('[data-out="' + name + '"]');
        for (var i = 0; i < els.length; i++) { els[i].textContent = text; }
    }
    function horses() {
        var value = Math.round(Number(number.value));
        if (!isFinite(value) || value < 1) { value = 1; }
        return Math.min(value, most);
    }
    function update(from) {
        var count = horses();
        if (from !== 'number') { number.value = count; }
        if (from !== 'slider') { slider.value = Math.min(count, large); }
        var pence = 0;
        var planName = '';
        var building = [];
        var i;
        for (i = 0; i < plans.length; i++) {
            if (plans[i].checked) { pence += Math.round(Number(plans[i].getAttribute('data-price')) * 100); planName = plans[i].getAttribute('data-name'); }
        }
        for (i = 0; i < addons.length; i++) {
            if (!addons[i].checked) { continue; }
            pence += Math.round(Number(addons[i].getAttribute('data-price')) * 100);
            building.push(addons[i].getAttribute('data-name'));
        }
        var monthly = pence * count / 100;
        var percent = building.length ? partner : standard;
        var offer = Math.round(monthly * (100 - percent)) / 100;
        put('plan', planName);
        put('monthly', money(monthly));
        put('per-horse', money(pence / 100));
        put('horses', count);
        put('offer-monthly', money(offer));
        put('saving', money((monthly - offer) * months));
        put('soon', building.join(' and '));
        put('percent', percent);
        form.querySelector('[data-offer="standard"]').hidden = building.length > 0;
        form.querySelector('[data-offer="partner"]').hidden = building.length === 0;
        slider.style.background = 'linear-gradient(to right, #3D5A63 ' + ((Math.min(count, large) - 1) / (large - 1) * 100) + '%, #E6E1D1 0)';
        slider.setAttribute('aria-valuetext', count + ' horses');
    }
    number.addEventListener('input', function () { update('number'); });
    number.addEventListener('change', function () { update(); });
    slider.addEventListener('input', function () { number.value = slider.value; update('slider'); });
    var steps = form.querySelectorAll('[data-step]');
    for (var s = 0; s < steps.length; s++) {
        steps[s].addEventListener('click', function () { number.value = horses() + Number(this.getAttribute('data-step')); update(); });
    }
    var inputs = form.querySelectorAll('[data-plan], [data-addon]');
    for (var m = 0; m < inputs.length; m++) { inputs[m].addEventListener('change', function () { update(); }); }

    // "Work out my price" on a plan card: pick that plan and go to the calculator, without a reload.
    var choose = document.querySelectorAll('[data-choose-plan]');
    for (var c = 0; c < choose.length; c++) {
        choose[c].addEventListener('click', function (e) {
            var key = this.getAttribute('data-choose-plan');
            for (var p = 0; p < plans.length; p++) { plans[p].checked = plans[p].value === key; }
            update();
            e.preventDefault();
            var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            document.getElementById('calculator').scrollIntoView({ behavior: still ? 'auto' : 'smooth' });
        });
    }
    update();

    // Phones: a bar with the price while the inputs are on screen and the result is not.
    var bar = form.querySelector('[data-calc-bar]');
    var fields = form.querySelector('[data-calc-inputs]');
    var result = document.getElementById('calc-result');
    if (bar && fields && result && 'IntersectionObserver' in window) {
        var fieldsShown = false;
        var resultShown = false;
        var watch = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.target === fields) { fieldsShown = entry.isIntersecting; } else { resultShown = entry.isIntersecting; }
            });
            bar.hidden = !(fieldsShown && !resultShown);
        });
        watch.observe(fields);
        watch.observe(result);
    }
})();

// Home hero: point at a stall (or tap it, or Tab to it) to see that horse's
// record (marketing/_horse_preview.html). One card at a time.
(function () {
    'use strict';
    var yard = document.querySelector('[data-yard]');
    if (!yard) { return; }
    var buttons = yard.querySelectorAll('[data-horse]');
    var open = null;
    var pinned = false;

    function show(key, pin) {
        hide();
        var card = yard.querySelector('[data-horse-card="' + key + '"]');
        var button = yard.querySelector('[data-horse="' + key + '"]');
        if (!card || !button) { return; }
        card.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        open = key;
        pinned = !!pin;
    }
    function hide() {
        if (!open) { return; }
        var card = yard.querySelector('[data-horse-card="' + open + '"]');
        var button = yard.querySelector('[data-horse="' + open + '"]');
        if (card) { card.hidden = true; }
        if (button) { button.setAttribute('aria-expanded', 'false'); }
        open = null;
        pinned = false;
    }
    for (var i = 0; i < buttons.length; i++) {
        buttons[i].addEventListener('mouseenter', function () { if (!pinned) { show(this.getAttribute('data-horse')); } });
        buttons[i].addEventListener('mouseleave', function () { if (!pinned) { hide(); } });
        buttons[i].addEventListener('focus', function () { show(this.getAttribute('data-horse')); });
        buttons[i].addEventListener('blur', function () { if (!pinned) { hide(); } });
        buttons[i].addEventListener('click', function (e) {
            var key = this.getAttribute('data-horse');
            if (open === key && pinned) { hide(); } else { show(key, true); }
            e.stopPropagation();
        });
    }
    document.addEventListener('click', function (e) { if (open && !e.target.closest('[data-horse-card]')) { hide(); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { hide(); } });
})();

// "See it working" (marketing/_showcase.html). Tabs light the matching
// side-bar item; [data-go] switches the view inside a tab (a list to a
// horse, an owner, an invoice; map to cards); [data-sub] switches a horse
// page's own tabs; the map picks a paddock.
// It also plays by itself: a pointer moves and clicks through a few
// seconds of real use in each tab, then the next tab. The first click, tap
// or key press in it stops that for good. It pauses off screen, has a
// Pause button, and never plays with less motion asked for.
(function () {
    'use strict';
    var box = document.querySelector('[data-showcase]');
    if (!box) { return; }
    var tabs = Array.prototype.slice.call(box.querySelectorAll('[data-tab]'));
    var navs = box.querySelectorAll('[data-nav]');
    var fields = box.querySelectorAll('[data-pick-field]');

    function one(sel) { return box.querySelector(sel); }
    function all(sel) { return Array.prototype.slice.call(box.querySelectorAll(sel)); }

    function select(tab, focus) {
        var key = tab.getAttribute('data-tab');
        tabs.forEach(function (t) {
            var on = t === tab;
            t.setAttribute('aria-selected', on ? 'true' : 'false');
            t.tabIndex = on ? 0 : -1;
            one('[data-panel="' + t.getAttribute('data-tab') + '"]').hidden = !on;
        });
        for (var n = 0; n < navs.length; n++) { navs[n].classList.toggle('is-on', navs[n].getAttribute('data-nav') === key); }
        if (focus) { tab.focus(); }
    }
    function go(panel, target, link) {
        all('[data-panel="' + panel + '"] [data-view]').forEach(function (v) { v.hidden = v.getAttribute('data-view') !== target; });
        var group = link && link.closest('[role="group"]');
        if (group) {
            group.querySelectorAll('[data-go]').forEach(function (p) { p.setAttribute('aria-pressed', p === link ? 'true' : 'false'); });
        }
    }
    function sub(view, name) {
        view.querySelectorAll('[data-sub]').forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-sub') === name ? 'true' : 'false'); });
        view.querySelectorAll('[data-subview]').forEach(function (s) { s.hidden = s.getAttribute('data-subview') !== name; });
    }
    function send(view, sent) {
        view.querySelectorAll('[data-demo="draft-badge"]').forEach(function (b) { b.hidden = sent; });
        view.querySelectorAll('[data-demo="sent-badge"]').forEach(function (b) { b.hidden = !sent; });
    }
    function pick(key) {
        for (var i = 0; i < fields.length; i++) { fields[i].setAttribute('aria-pressed', fields[i].getAttribute('data-pick-field') === key ? 'true' : 'false'); }
        all('[data-field-info]').forEach(function (info) { info.hidden = info.getAttribute('data-field-info') !== key; });
    }

    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () { select(this); });
        tab.addEventListener('keydown', function (e) {
            var at = tabs.indexOf(this);
            if (e.key === 'ArrowRight') { select(tabs[(at + 1) % tabs.length], true); e.preventDefault(); }
            if (e.key === 'ArrowLeft') { select(tabs[(at - 1 + tabs.length) % tabs.length], true); e.preventDefault(); }
        });
    });
    box.addEventListener('click', function (e) {
        var link = e.target.closest('[data-go]');
        if (link) { go(link.closest('[data-panel]').getAttribute('data-panel'), link.getAttribute('data-go'), link); return; }
        var tabButton = e.target.closest('[data-sub]');
        if (tabButton) { sub(tabButton.closest('[data-view]'), tabButton.getAttribute('data-sub')); return; }
        var sendButton = e.target.closest('[data-send]');
        if (sendButton) { send(sendButton.closest('[data-view]'), true); }
    });
    for (var f = 0; f < fields.length; f++) {
        fields[f].addEventListener('click', function () { pick(this.getAttribute('data-pick-field')); });
        fields[f].addEventListener('mouseenter', function () { if (!playing) { pick(this.getAttribute('data-pick-field')); } });
        fields[f].addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { pick(this.getAttribute('data-pick-field')); e.preventDefault(); }
        });
    }

    // ── Playing by itself ──
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var toggle = one('[data-autoplay-toggle]');
    var label = one('[data-autoplay-label]');
    var bar = one('[data-demo-progress]');
    var windowEl = one('[data-demo-window]');
    var area = one('[data-cursor-area]');
    var cursor = one('[data-cursor]');
    var ripple = one('[data-cursor-ripple]');
    var playing = false;
    var stopped = false;
    var onScreen = false;
    var timers = [];
    var current = 0;

    function later(ms, fn) { timers.push(setTimeout(fn, ms)); }
    function clear() { timers.forEach(clearTimeout); timers = []; }
    function tap(el) { if (!el) { return; } el.classList.remove('mk-tap'); void el.offsetWidth; el.classList.add('mk-tap'); }
    function seen(el) { return el && el.getClientRects().length > 0; }

    // Move the pointer to an element, click it (a ripple), then do the action.
    function clickOn(el, action) {
        if (!seen(el) || !cursor || !area) { if (action) { action(); } return; }
        var a = area.getBoundingClientRect();
        var r = el.getBoundingClientRect();
        var x = r.left - a.left + Math.min(r.width / 2, 40);
        var y = r.top - a.top + r.height / 2;
        cursor.classList.add('is-on');
        cursor.style.transform = 'translate(' + x + 'px,' + y + 'px)';
        later(750, function () {
            if (ripple) { ripple.classList.remove('is-click'); void ripple.offsetWidth; ripple.classList.add('is-click'); }
            tap(el);
            later(220, function () { if (action) { action(); } });
        });
    }

    function countUp(el) {
        var text = el.getAttribute('data-final') || el.textContent;
        el.setAttribute('data-final', text);
        var m = text.match(/^(\D*)([\d,]+(?:\.\d+)?)(.*)$/);
        if (!m) { return; }
        var target = Number(m[2].replace(/,/g, ''));
        var decimals = (m[2].split('.')[1] || '').length;
        var start = performance.now();
        (function step(now) {
            var t = Math.min(1, (now - start) / 900);
            var value = (target * (1 - Math.pow(1 - t, 3))).toLocaleString('en-GB', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
            el.textContent = m[1] + value + m[3];
            if (t < 1 && playing) { requestAnimationFrame(step); } else { el.textContent = text; }
        })(start);
    }

    // Each scene: how long it plays, a reset to its first state, and its steps.
    var scenes = {
        dashboard: { ms: 9000, reset: function () {
            var fresh = one('[data-demo="timeline-new"]');
            if (fresh) { fresh.hidden = true; }
            var rec = one('[data-demo="record"]');
            if (rec) { rec.textContent = rec.getAttribute('data-text'); rec.classList.remove('bg-meadow'); rec.classList.add('bg-saddle'); }
        }, steps: function () {
            all('[data-count]').forEach(countUp);
            later(2400, function () {
                var rec = one('[data-demo="record"]');
                clickOn(rec, function () { rec.textContent = 'Recorded ✓'; rec.classList.remove('bg-saddle'); rec.classList.add('bg-meadow'); });
            });
            later(4800, function () { var fresh = one('[data-demo="timeline-new"]'); if (fresh) { fresh.hidden = false; } });
        } },
        horses: { ms: 16000, reset: function () {
            go('horses', 'list');
            all('[data-panel="horses"] [data-view]').forEach(function (v) { if (v.querySelector('[data-sub]')) { sub(v, 'overview'); } });
        }, steps: function () {
            later(400, function () { clickOn(one('[data-nav="horses"]')); });
            later(2000, function () { clickOn(one('[data-view="list"] [data-go="horse-bramble"]'), function () { go('horses', 'horse-bramble'); }); });
            later(4600, function () { clickOn(one('[data-view="horse-bramble"] [data-go="horse-duke"]'), function () { go('horses', 'horse-duke'); }); });
            later(6800, function () { clickOn(one('[data-view="horse-duke"] [data-go="horse-pip"]'), function () { go('horses', 'horse-pip'); }); });
            later(9000, function () { var v = one('[data-view="horse-pip"]'); clickOn(v.querySelector('[data-sub="timeline"]'), function () { sub(v, 'timeline'); }); });
            later(12400, function () { var v = one('[data-view="horse-pip"]'); clickOn(v.querySelector('[data-sub="breeding"]'), function () { sub(v, 'breeding'); }); });
        } },
        maps: { ms: 11000, reset: function () {
            go('maps', 'map', one('[data-panel="maps"] [data-go="map"]'));
            pick('top');
            var mover = one('[data-demo="mover"]');
            if (mover) { mover.style.transform = ''; }
        }, steps: function () {
            later(400, function () { clickOn(one('[data-nav="maps"]')); });
            later(1800, function () { var m = one('[data-demo="mover"]'); if (m) { m.style.transform = 'translate(118px, 22px)'; } });
            later(3800, function () { clickOn(one('[data-pick-field="meadow"]'), function () { pick('meadow'); }); });
            later(6200, function () { clickOn(one('[data-pick-field="winter"]'), function () { pick('winter'); }); });
            later(8600, function () { var b = one('[data-panel="maps"] [data-go="cards"]'); clickOn(b, function () { go('maps', 'cards', b); }); });
        } },
        owners: { ms: 15000, reset: function () {
            go('owners', 'list');
            all('[data-panel="owners"] [data-view^="new-"]').forEach(function (v) { send(v, false); });
        }, steps: function () {
            later(400, function () { clickOn(one('[data-nav="owners"]')); });
            later(1800, function () { clickOn(one('[data-panel="owners"] [data-view="list"] [data-go="owner-amy"]'), function () { go('owners', 'owner-amy'); }); });
            later(4800, function () { clickOn(one('[data-view="owner-amy"] [data-go="create-amy"]'), function () { go('owners', 'create-amy'); }); });
            later(8400, function () { clickOn(one('[data-view="create-amy"] [data-go="new-amy"]'), function () { go('owners', 'new-amy'); }); });
            later(11200, function () { var v = one('[data-view="new-amy"]'); clickOn(v.querySelector('[data-send]'), function () { send(v, true); }); });
        } },
        invoicing: { ms: 11000, reset: function () {
            go('invoicing', 'list');
            all('[data-demo-row]').forEach(function (r) { r.hidden = false; r.classList.remove('mk-row-in'); r.style.animationDelay = ''; });
        }, steps: function () {
            var rows = all('[data-demo-row]');
            later(400, function () { clickOn(one('[data-demo="generate"]'), function () { rows.forEach(function (r) { r.hidden = true; }); }); });
            later(1800, function () {
                rows.forEach(function (r, i) { r.style.animationDelay = (i * 180) + 'ms'; r.hidden = false; r.classList.add('mk-row-in'); });
            });
            later(5200, function () { clickOn(one('[data-panel="invoicing"] [data-go="inv6"]'), function () { go('invoicing', 'inv6'); }); });
        } }
    };

    function playScene(index) {
        clear();
        current = index % tabs.length;
        var tab = tabs[current];
        var scene = scenes[tab.getAttribute('data-tab')];
        select(tab);
        scene.reset();
        if (bar) {
            bar.style.transition = 'none';
            bar.style.width = '0';
            void bar.offsetWidth;
            bar.style.transition = 'width ' + scene.ms + 'ms linear';
            bar.style.width = '100%';
        }
        scene.steps();
        later(scene.ms, function () { playScene(current + 1); });
    }
    function setLabel() { if (label) { label.textContent = playing ? 'Pause' : 'Play'; } if (toggle) { toggle.setAttribute('aria-pressed', playing ? 'false' : 'true'); } }
    function start() {
        if (stopped || playing || !onScreen) { return; }
        playing = true;
        setLabel();
        playScene(current);
    }
    function pause() {
        playing = false;
        clear();
        // A scene may have hidden the invoice rows to show them arriving.
        all('[data-demo-row]').forEach(function (r) { r.hidden = false; });
        if (cursor) { cursor.classList.remove('is-on'); }
        if (bar) { bar.style.transition = 'none'; bar.style.width = '0'; }
        setLabel();
    }
    // The visitor takes over: the demo stops for good, everything stays usable.
    function takeOver() {
        if (stopped) { return; }
        stopped = true;
        pause();
        if (toggle) { toggle.hidden = true; }
    }

    if (still || !('IntersectionObserver' in window) || !windowEl) { return; }
    if (toggle) {
        toggle.hidden = false;
        toggle.addEventListener('click', function (e) {
            e.stopPropagation();
            if (playing) { pause(); stopped = true; } else { stopped = false; start(); }
        });
    }
    ['pointerdown', 'keydown'].forEach(function (type) {
        box.addEventListener(type, function (e) { if (e.isTrusted && !e.target.closest('[data-autoplay-toggle]')) { takeOver(); } }, true);
    });
    new IntersectionObserver(function (entries) {
        onScreen = entries[0].isIntersecting;
        if (onScreen) { start(); } else if (playing) { pause(); }
    }, { threshold: 0.4 }).observe(windowEl);
})();
