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
