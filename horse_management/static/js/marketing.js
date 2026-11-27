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
        return Math.min(value, 2000);
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
        form.querySelector('[data-large-yard]').hidden = count < large;
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
