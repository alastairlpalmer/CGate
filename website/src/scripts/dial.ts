/*
  The dial. docs/plan.md, Section 12.

  Five links on a ring. The centre slot names where the visitor is. The DOM
  order always matches the visual order, so keyboard focus follows the eye.
  A turn reorders the DOM, then GSAP Flip animates the difference.

  State is one number: which item sits at the centre. Everything else is
  derived. Slots are set server side in data-slot; this script only changes
  them. No inline styles for position.

  Also serves the static index (the one-flag fallback): the marker-following
  part is shared, the turning part only runs when data-dial is present.
*/
import { Flip } from 'gsap/Flip';
import { navigate } from 'astro:transitions/client';
import { gsap, EASE, prefersReducedMotion } from './motion';

gsap.registerPlugin(Flip);

const ONE = 0.52;
const TWO = 0.7;
const SMALL = '(max-width: 619px)';

type Item = HTMLLIElement;

interface Ring {
  nav: HTMLElement;
  ul: HTMLUListElement;
  announce: HTMLElement | null;
  template: string;
  turning: boolean;
}

function items(ul: HTMLUListElement): Item[] {
  return Array.from(ul.querySelectorAll<Item>('[data-slot]'));
}

function slotOf(li: Item): number {
  return Number(li.dataset.slot);
}

function centreOf(ul: HTMLUListElement): Item | undefined {
  return items(ul).find((li) => slotOf(li) === 0);
}

function applySlots(ul: HTMLUListElement) {
  items(ul).forEach((li, i) => {
    li.dataset.slot = String(i - 2);
  });
}

/* Move the ring so the item currently at slot `s` ends at slot 0. */
function reorder(ul: HTMLUListElement, s: number) {
  const list = items(ul);
  const n = list.length;
  const shift = ((s % n) + n) % n;
  const next = [...list.slice(shift), ...list.slice(0, shift)];
  next.forEach((li) => ul.appendChild(li));
  applySlots(ul);
}

function slotOpacity(li: Item): number {
  const v = getComputedStyle(li).getPropertyValue('--slot-opacity').trim();
  return v ? Number(v) : 1;
}

function normalisePath(p: string): string {
  return p.replace(/\/+$/, '') || '/';
}

function itemForPath(ul: HTMLUListElement, pathname: string): Item | undefined {
  const path = normalisePath(pathname);
  const list = items(ul);
  return (
    list.find((li) => normalisePath(li.dataset.href ?? '') === path) ??
    list.find((li) => {
      const href = normalisePath(li.dataset.href ?? '');
      return href !== '/' && path.startsWith(href + '/');
    }) ??
    list.find((li) => normalisePath(li.dataset.href ?? '') === '/')
  );
}

function setState(ring: Ring) {
  const centre = centreOf(ring.ul);
  const selected = Boolean(centre?.dataset.marker);
  ring.nav.dataset.state = selected ? 'selected' : 'rest';
  items(ring.ul).forEach((li) => {
    if (selected) {
      li.toggleAttribute('data-active', li === centre);
    }
  });
}

function say(ring: Ring, label: string) {
  if (!ring.announce) return;
  ring.announce.textContent = ring.template.replace('{label}', label);
}

function emit(centre: Item) {
  document.dispatchEvent(
    new CustomEvent('dial:select', {
      detail: { marker: centre.dataset.marker ?? null, href: centre.dataset.href ?? null },
    }),
  );
}

/* Turn the ring so `target` comes to the centre. Resolves when the turn ends. */
function select(ring: Ring, target: Item, animate = true): Promise<void> {
  const s = slotOf(target);
  if (s === 0 || ring.turning) return Promise.resolve();

  const list = items(ring.ul);
  const wrapping = s > 0 ? list.slice(0, s) : list.slice(s);
  const moving = list.filter((li) => !wrapping.includes(li));
  const focused = document.activeElement as HTMLElement | null;
  const keepFocus = focused && ring.ul.contains(focused) ? focused : null;

  const finish = () => {
    ring.turning = false;
    setState(ring);
    const label = target.querySelector('a')?.textContent?.trim() ?? '';
    say(ring, label);
    emit(target);
    keepFocus?.focus({ preventScroll: true });
  };

  if (!animate || prefersReducedMotion()) {
    reorder(ring.ul, s);
    finish();
    return Promise.resolve();
  }

  ring.turning = true;
  const duration = Math.abs(s) === 1 ? ONE : TWO;
  const state = Flip.getState(moving, { props: 'opacity' });
  reorder(ring.ul, s);

  /* The item that wraps round must not slide through the middle. It appears
     at its new end, then fades in over the last 40% of the turn. */
  gsap.set(wrapping, { opacity: 0 });
  wrapping.forEach((li) => {
    gsap.to(li, {
      opacity: slotOpacity(li),
      duration: duration * 0.4,
      delay: duration * 0.6,
      ease: EASE,
      clearProps: 'opacity',
    });
  });

  return new Promise((resolve) => {
    Flip.from(state, {
      duration,
      ease: EASE,
      scale: true,
      props: 'opacity',
      clearProps: 'transform,opacity',
      onComplete: () => {
        finish();
        resolve();
      },
    });
  });
}

function setupRing(nav: HTMLElement) {
  const ul = nav.querySelector<HTMLUListElement>('[data-dial-ring]');
  if (!ul || ul.dataset.ready === 'true') return;
  ul.dataset.ready = 'true';

  const ring: Ring = {
    nav,
    ul,
    announce: nav.querySelector<HTMLElement>('[data-dial-announce]'),
    template: nav.querySelector<HTMLElement>('[data-dial-announce]')?.dataset.announceTemplate ?? '{label}',
    turning: false,
  };
  setState(ring);

  /* Click: bring the item to the centre, then follow the link. On the current
     page the turn is all that happens. */
  ul.addEventListener('click', (event) => {
    const a = (event.target as HTMLElement).closest('a');
    const li = a?.closest<Item>('[data-slot]');
    if (!a || !li) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    if (slotOf(li) === 0) return;

    event.preventDefault();
    const samePage = normalisePath(new URL(a.href).pathname) === normalisePath(location.pathname);
    select(ring, li).then(() => {
      if (!samePage) navigate(a.href);
    });
  });

  /* Keyboard. Left and right turn one detent and carry focus. Home returns About us. */
  ul.addEventListener('keydown', (event) => {
    const list = items(ul);
    let target: Item | undefined;
    if (event.key === 'ArrowRight') target = list.find((li) => slotOf(li) === 1);
    else if (event.key === 'ArrowLeft') target = list.find((li) => slotOf(li) === -1);
    else if (event.key === 'Home') target = list.find((li) => !li.dataset.marker);
    if (!target) return;
    event.preventDefault();
    select(ring, target);
  });

  /* Swipe, on small screens only. One swipe turns one detent. */
  let startX: number | null = null;
  ul.addEventListener('pointerdown', (event) => {
    if (!window.matchMedia(SMALL).matches) return;
    startX = event.clientX;
  });
  ul.addEventListener('pointerup', (event) => {
    if (startX === null) return;
    const dx = event.clientX - startX;
    startX = null;
    if (Math.abs(dx) < 40) return;
    const list = items(ul);
    const target = list.find((li) => slotOf(li) === (dx < 0 ? 1 : -1));
    if (target) select(ring, target);
  });
  ul.addEventListener('pointercancel', () => {
    startX = null;
  });

  /* The banner's rotation moves the marker while the dial rests. */
  document.addEventListener('hero:select', (event) => {
    if (ring.nav.dataset.state !== 'rest') return;
    const marker = (event as CustomEvent<{ marker: string }>).detail.marker;
    items(ul).forEach((li) => li.toggleAttribute('data-active', li.dataset.marker === marker));
  });

  /* The banner may have started before this ran. Catch up. */
  const current = document.querySelector<HTMLElement>('[data-hero]')?.dataset.current;
  if (current && ring.nav.dataset.state === 'rest') {
    items(ul).forEach((li) => li.toggleAttribute('data-active', li.dataset.marker === current));
  }

  /* After a client-side navigation the route decides the centre, instantly. */
  document.addEventListener('astro:after-swap', () => {
    const target = itemForPath(ul, location.pathname);
    if (target && slotOf(target) !== 0) {
      reorder(ul, slotOf(target));
    }
    setState(ring);
    items(ul).forEach((li) => {
      const a = li.querySelector('a');
      if (a) a.toggleAttribute('aria-current', slotOf(li) === 0 && normalisePath(a.pathname) === normalisePath(location.pathname));
    });
  });
}

/* The static index: only the marker follows the banner. */
function setupStatic(nav: HTMLElement) {
  if (nav.dataset.ready === 'true') return;
  nav.dataset.ready = 'true';
  document.addEventListener('hero:select', (event) => {
    const marker = (event as CustomEvent<{ marker: string }>).detail.marker;
    nav.querySelectorAll<HTMLElement>('[data-marker]').forEach((li) => {
      li.toggleAttribute('data-active', li.dataset.marker === marker);
    });
  });
}

function setup() {
  document.querySelectorAll<HTMLElement>('[data-index]').forEach((nav) => {
    if (nav.hasAttribute('data-dial')) setupRing(nav);
    else setupStatic(nav);
  });
}

document.addEventListener('astro:page-load', setup);
