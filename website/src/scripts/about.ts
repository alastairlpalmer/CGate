/*
  The About us paragraph. docs/plan.md, Section 11.4 H.
  Lines rise into view behind a mask, one after another, when the paragraph
  enters the viewport. Used once on the whole site. Split by line, never by
  character. Under reduced motion the text is simply there.
*/
import { SplitText } from 'gsap/SplitText';
import { gsap, EASE, NO_PREFERENCE } from './motion';

gsap.registerPlugin(SplitText);

const mm = gsap.matchMedia();
let split: SplitText | undefined;
let observer: IntersectionObserver | undefined;

function setup() {
  const lead = document.querySelector<HTMLElement>('[data-about-lead]');
  if (!lead) return;

  mm.add(NO_PREFERENCE, () => {
    let tween: gsap.core.Tween | undefined;
    let shown = false;

    document.fonts.ready.then(() => {
      split = SplitText.create(lead, {
        type: 'lines',
        mask: 'lines',
        autoSplit: true,
        onSplit(self) {
          tween = gsap.from(self.lines, {
            yPercent: 100,
            duration: 0.8,
            stagger: 0.08,
            ease: EASE,
            paused: !shown,
          });
          return tween;
        },
      });

      observer = new IntersectionObserver(
        (entries) => {
          if (!entries[0]?.isIntersecting) return;
          shown = true;
          tween?.play();
          observer?.disconnect();
        },
        { threshold: 0.3 },
      );
      observer.observe(lead);
    });

    return () => {
      observer?.disconnect();
      split?.revert();
      split = undefined;
    };
  });
}

document.addEventListener('astro:page-load', setup);
document.addEventListener('astro:before-swap', () => mm.revert());
