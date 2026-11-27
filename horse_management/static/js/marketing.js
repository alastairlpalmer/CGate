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

// Pricing: the horse-count slider updates each plan's monthly total.
(function () {
    'use strict';
    var slider = document.querySelector('[data-horse-slider]');
    if (!slider) { return; }
    var count = document.querySelectorAll('[data-horse-count]');
    var totals = document.querySelectorAll('[data-per-horse]');
    var large = document.querySelector('[data-large-yard]');
    var max = Number(slider.max);

    function money(value) {
        var whole = Math.round(value * 100) % 100 === 0;
        return '£' + value.toLocaleString('en-GB', { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 });
    }
    function update() {
        var horses = Number(slider.value);
        var i;
        for (i = 0; i < count.length; i++) { count[i].textContent = horses >= max ? max + '+' : horses; }
        for (i = 0; i < totals.length; i++) {
            totals[i].textContent = money(Number(totals[i].getAttribute('data-per-horse')) * horses);
        }
        if (large) { large.hidden = horses < max; }
        slider.style.background = 'linear-gradient(to right, #3D5A63 ' + ((horses - slider.min) / (max - slider.min) * 100) + '%, #E6E1D1 0)';
        slider.setAttribute('aria-valuetext', horses + ' horses');
    }
    slider.addEventListener('input', update);
    update();
})();
