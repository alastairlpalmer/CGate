/*
  Photograph galleries. docs/plan.md, Section 11.4 K.
  Embla again, already loaded. Lightbox with the native <dialog> element.
  Under reduced motion navigation works with no animation.
*/
import EmblaCarousel, { type EmblaCarouselType } from 'embla-carousel';
import { prefersReducedMotion } from './motion';

let instances: EmblaCarouselType[] = [];

function setupOne(block: HTMLElement) {
  const viewport = block.querySelector<HTMLElement>('[data-gallery-viewport]');
  if (!viewport) return;
  const prev = block.querySelector<HTMLButtonElement>('[data-gallery-prev]');
  const next = block.querySelector<HTMLButtonElement>('[data-gallery-next]');
  const count = block.querySelector<HTMLElement>('[data-gallery-count]');
  const lightbox = block.querySelector<HTMLDialogElement>('[data-gallery-lightbox]');
  const body = block.querySelector<HTMLElement>('[data-gallery-lightbox-body]');
  const template = block.dataset.galleryCount ?? '{n} of {total}';

  const embla = EmblaCarousel(viewport, { loop: false, duration: prefersReducedMotion() ? 0 : 25 });
  instances.push(embla);

  const update = () => {
    if (prev) prev.disabled = !embla.canScrollPrev();
    if (next) next.disabled = !embla.canScrollNext();
    if (count) {
      count.textContent = template
        .replace('{n}', String(embla.selectedScrollSnap() + 1))
        .replace('{total}', String(embla.scrollSnapList().length));
    }
  };
  embla.on('select', update);
  embla.on('reInit', update);
  update();

  prev?.addEventListener('click', () => embla.scrollPrev());
  next?.addEventListener('click', () => embla.scrollNext());

  block.querySelectorAll<HTMLButtonElement>('[data-gallery-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const img = button.querySelector('img');
      if (!img || !lightbox || !body) return;
      body.replaceChildren(img.cloneNode(true));
      lightbox.showModal();
    });
  });
  lightbox?.addEventListener('close', () => body?.replaceChildren());
}

function setup() {
  document.querySelectorAll<HTMLElement>('[data-gallery]').forEach(setupOne);
}

function teardown() {
  instances.forEach((e) => e.destroy());
  instances = [];
}

document.addEventListener('astro:page-load', setup);
document.addEventListener('astro:before-swap', teardown);
