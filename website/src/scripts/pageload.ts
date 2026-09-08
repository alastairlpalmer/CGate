/*
  The one page-load sequence. docs/plan.md, Section 11.4 A.
  Index items settle first, then the banner rises from a slight blur and
  scale. About 700 ms, then nothing. Under reduced motion everything is
  simply there. Nothing starts before the first photograph has painted.
*/
import { gsap, EASE, NO_PREFERENCE } from './motion';

const mm = gsap.matchMedia();

async function firstImagePainted(frame: HTMLElement): Promise<void> {
  const img = frame.querySelector<HTMLImageElement>('img');
  if (!img) return;
  const decode = img.decode().catch(() => undefined);
  const timeout = new Promise<void>((resolve) => setTimeout(resolve, 1500));
  await Promise.race([decode, timeout]);
}

function setup() {
  const header = document.querySelector<HTMLElement>('[data-header]');
  const frame = document.querySelector<HTMLElement>('[data-hero-frame]');
  const items = header ? Array.from(header.querySelectorAll<HTMLElement>('[data-index] li')) : [];
  const firstLoad = header ? header.dataset.loaded !== 'true' : false;
  if (header) header.dataset.loaded = 'true';

  mm.add(NO_PREFERENCE, () => {
    const run = async () => {
      if (frame) await firstImagePainted(frame);
      const tl = gsap.timeline({ defaults: { ease: EASE } });
      if (firstLoad && items.length) {
        tl.from(items, { opacity: 0, duration: 0.4, stagger: 0.04, clearProps: 'opacity' }, 0);
      }
      if (frame) {
        tl.from(frame, { scale: 1.06, filter: 'blur(8px)', duration: 0.5, clearProps: 'all' }, firstLoad ? 0.2 : 0);
      }
    };
    run();
  });
}

document.addEventListener('astro:page-load', setup);
document.addEventListener('astro:before-swap', () => mm.revert());
