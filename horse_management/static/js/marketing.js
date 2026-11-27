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

// "See it working" (marketing/_showcase.html): tabs light the matching
// side-bar item; [data-go] links switch the view inside a tab (a list to
// a horse or an invoice, map to cards); the map picks a paddock.
(function () {
    'use strict';
    var box = document.querySelector('[data-showcase]');
    if (!box) { return; }
    var tabs = box.querySelectorAll('[data-tab]');
    var navs = box.querySelectorAll('[data-nav]');

    function select(tab, focus) {
        var key = tab.getAttribute('data-tab');
        for (var i = 0; i < tabs.length; i++) {
            var on = tabs[i] === tab;
            tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
            tabs[i].tabIndex = on ? 0 : -1;
            box.querySelector('[data-panel="' + tabs[i].getAttribute('data-tab') + '"]').hidden = !on;
        }
        for (var n = 0; n < navs.length; n++) { navs[n].classList.toggle('is-on', navs[n].getAttribute('data-nav') === key); }
        if (focus) { tab.focus(); }
    }
    for (var t = 0; t < tabs.length; t++) {
        tabs[t].addEventListener('click', function () { select(this); });
        tabs[t].addEventListener('keydown', function (e) {
            var list = Array.prototype.slice.call(tabs);
            var at = list.indexOf(this);
            if (e.key === 'ArrowRight') { select(list[(at + 1) % list.length], true); e.preventDefault(); }
            if (e.key === 'ArrowLeft') { select(list[(at - 1 + list.length) % list.length], true); e.preventDefault(); }
        });
    }

    box.addEventListener('click', function (e) {
        var link = e.target.closest('[data-go]');
        if (!link) { return; }
        var panel = link.closest('[data-panel]');
        var target = link.getAttribute('data-go');
        var views = panel.querySelectorAll('[data-view]');
        for (var v = 0; v < views.length; v++) { views[v].hidden = views[v].getAttribute('data-view') !== target; }
        var group = link.closest('[role="group"]');
        if (group) {
            var peers = group.querySelectorAll('[data-go]');
            for (var p = 0; p < peers.length; p++) { peers[p].setAttribute('aria-pressed', peers[p] === link ? 'true' : 'false'); }
        }
        var heading = panel.querySelector('[data-view="' + target + '"] button[data-go]');
        if (heading && target !== 'list' && target !== 'map' && target !== 'cards') { heading.focus(); }
    });

    var fields = box.querySelectorAll('[data-pick-field]');
    function pick(key) {
        for (var i = 0; i < fields.length; i++) { fields[i].setAttribute('aria-pressed', fields[i].getAttribute('data-pick-field') === key ? 'true' : 'false'); }
        var infos = box.querySelectorAll('[data-field-info]');
        for (var j = 0; j < infos.length; j++) { infos[j].hidden = infos[j].getAttribute('data-field-info') !== key; }
    }
    for (var f = 0; f < fields.length; f++) {
        fields[f].addEventListener('click', function () { pick(this.getAttribute('data-pick-field')); });
        fields[f].addEventListener('mouseenter', function () { pick(this.getAttribute('data-pick-field')); });
        fields[f].addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { pick(this.getAttribute('data-pick-field')); e.preventDefault(); }
        });
    }
})();
