/*
  The banner. docs/plan.md, Sections 3.1 and 11.4 C.
  Embla with the Fade, Autoplay and ClassNames plugins. A 6-second hold, a
  slow cross-fade. Pauses on hover and on keyboard focus. Stops for good once
  the visitor takes control. Never starts under reduced motion.
*/
import EmblaCarousel, { type EmblaCarouselType } from 'embla-carousel';
import Fade from 'embla-carousel-fade';
import Autoplay, { type AutoplayType } from 'embla-carousel-autoplay';
import ClassNames from 'embla-carousel-class-names';
import { prefersReducedMotion } from './motion';

const HOLD = 6000;

let embla: EmblaCarouselType | undefined;
let observer: IntersectionObserver | undefined;
let cleanup: (() => void)[] = [];

function setup() {
  const root = document.querySelector<HTMLElement>('[data-hero]');
  const viewport = root?.querySelector<HTMLElement>('[data-hero-viewport]');
  if (!root || !viewport) return;

  const slides = Array.from(viewport.querySelectorAll<HTMLElement>('.hero__slide'));
  const dots = Array.from(root.querySelectorAll<HTMLButtonElement>('[data-hero-dot]'));
  const pauseButton = root.querySelector<HTMLButtonElement>('[data-hero-pause]');
  const reduced = prefersReducedMotion();
  const single = slides.length < 2;

  const autoplay: AutoplayType | undefined =
    reduced || single
      ? undefined
      : Autoplay({
          delay: HOLD,
          stopOnInteraction: false,
          stopOnMouseEnter: true,
          stopOnFocusIn: true,
          playOnInit: true,
        });

  const plugins = [Fade(), ClassNames()];
  if (autoplay) plugins.push(autoplay);

  embla = EmblaCarousel(
    viewport,
    { loop: true, duration: reduced ? 0 : 45, watchDrag: !single, skipSnaps: false },
    plugins,
  );

  /* Once the visitor takes control, rotation does not come back. */
  let locked = !autoplay;
  let pausedByVisitor = false;

  const lock = () => {
    locked = true;
    autoplay?.stop();
    if (pauseButton) pauseButton.hidden = true;
  };

  embla.on('autoplay:play', () => {
    if (locked || pausedByVisitor) autoplay?.stop();
  });

  const update = () => {
    const i = embla!.selectedScrollSnap();
    dots.forEach((dot, n) => dot.setAttribute('aria-pressed', n === i ? 'true' : 'false'));
    const marker = slides[i]?.dataset.marker ?? null;
    root.dataset.current = marker ?? '';
    document.dispatchEvent(new CustomEvent('hero:select', { detail: { marker } }));
  };
  embla.on('select', update);
  update();

  dots.forEach((dot) => {
    dot.addEventListener('click', () => {
      lock();
      embla?.scrollTo(Number(dot.dataset.heroDot));
    });
  });

  if (pauseButton) {
    if (!autoplay) pauseButton.hidden = true;
    pauseButton.addEventListener('click', () => {
      pausedByVisitor = !pausedByVisitor;
      pauseButton.setAttribute('aria-pressed', pausedByVisitor ? 'true' : 'false');
      pauseButton.setAttribute(
        'aria-label',
        (pausedByVisitor ? pauseButton.dataset.playLabel : pauseButton.dataset.pauseLabel) ?? '',
      );
      if (pausedByVisitor) autoplay?.stop();
      else autoplay?.play();
    });
  }

  /* The dial turning to a business locks the banner to it. */
  const onDial = (event: Event) => {
    const marker = (event as CustomEvent<{ marker: string | null }>).detail.marker;
    if (!marker) return;
    const index = slides.findIndex((s) => s.dataset.marker === marker);
    if (index < 0) return;
    lock();
    embla?.scrollTo(index);
  };
  document.addEventListener('dial:select', onDial);
  cleanup.push(() => document.removeEventListener('dial:select', onDial));

  /* Off screen, the timer stops. Back on screen, it resumes unless the visitor stopped it. */
  if (autoplay) {
    observer = new IntersectionObserver(
      (entries) => {
        const visible = entries[0]?.isIntersecting ?? true;
        if (!visible) autoplay.stop();
        else if (!locked && !pausedByVisitor) autoplay.play();
      },
      { threshold: 0.2 },
    );
    observer.observe(root);
  }
}

function teardown() {
  observer?.disconnect();
  observer = undefined;
  embla?.destroy();
  embla = undefined;
  cleanup.forEach((fn) => fn());
  cleanup = [];
}

document.addEventListener('astro:page-load', setup);
document.addEventListener('astro:before-swap', teardown);
