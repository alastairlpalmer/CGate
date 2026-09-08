/*
  One GSAP setup for the whole site. One ease. One reduced-motion check.
  Import from here; never import gsap directly in another script.
*/
import { gsap } from 'gsap';
import { CustomEase } from 'gsap/CustomEase';

gsap.registerPlugin(CustomEase);

/* The shared site curve, cubic-bezier(0.22, 1, 0.36, 1). Registered once. */
export const EASE = 'site';
if (!CustomEase.get(EASE)) {
  CustomEase.create(EASE, '0.22, 1, 0.36, 1');
}

export const REDUCED = '(prefers-reduced-motion: reduce)';
export const NO_PREFERENCE = '(prefers-reduced-motion: no-preference)';

export function prefersReducedMotion(): boolean {
  return window.matchMedia(REDUCED).matches;
}

export { gsap };
