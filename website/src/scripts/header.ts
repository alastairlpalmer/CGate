/*
  Sticky header state. Browsers that understand scroll-driven animation do
  this in CSS. This is the fallback: an IntersectionObserver on the banner
  sets data-scrolled on the header once the banner has left the viewport.
  It also runs where scroll-driven animation is supported, so the header
  knows its state for any script that asks.
*/
let observer: IntersectionObserver | undefined;

function setup() {
  const header = document.querySelector<HTMLElement>('[data-header]');
  if (!header) return;
  observer?.disconnect();

  const hero = document.querySelector<HTMLElement>('[data-hero-section]');
  if (!hero) {
    header.dataset.scrolled = 'true';
    return;
  }

  const headerHeight = header.getBoundingClientRect().height;
  observer = new IntersectionObserver(
    (entries) => {
      const entry = entries[0];
      header.dataset.scrolled = entry && entry.isIntersecting ? 'false' : 'true';
    },
    { rootMargin: `-${Math.round(headerHeight)}px 0px 0px 0px`, threshold: 0 },
  );
  observer.observe(hero);
}

document.addEventListener('astro:page-load', setup);
document.addEventListener('astro:before-swap', () => observer?.disconnect());
