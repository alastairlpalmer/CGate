/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
  ],
  safelist: [
    // Classes used in Alpine.js :class bindings that Tailwind JIT can't detect
    'bg-info-blue', 'text-info-blue', 'border-l-info-blue',
    'bg-saddle', 'text-saddle', 'border-l-saddle',
    'bg-error-red', 'text-error-red', 'border-l-error-red',
    'bg-sand', 'text-sand', 'border-l-sand',
    'bg-sage', 'bg-forest',
  ],
  theme: {
    // The palette is defined here (not under `extend`) on purpose: Tailwind's
    // default colours (gray-*, red-*, amber-*, …) are switched off, so a class
    // that bypasses the brand tokens generates no CSS and shows up unstyled
    // instead of quietly shifting hue from page to page.
    colors: {
      inherit: 'inherit',
      current: 'currentColor',
      transparent: 'transparent',
      black: '#000',
      white: '#fff',
      // Brand primary — "Brim" (muted dark teal/slate blue-green).
      // Token name kept as `forest` so existing bg-forest/text-forest usages repoint cleanly.
      forest: {
        DEFAULT: '#3D5A63',
        light: '#4F727D',
      },
      // Brim alias for new/semantic usage.
      brim: {
        DEFAULT: '#3D5A63',
        light: '#4F727D',
      },
      // Secondary — muted teal tint of Brim (re-derived from the old sage green).
      sage: {
        DEFAULT: '#6A8990',
        50: '#EFF3F4',
        100: '#DBE4E6',
        200: '#BCCCD0',
        300: '#9CB2B8',
        400: '#819DA4',
        light: '#88A6AD',
      },
      // Warm cream — "Crown" panels/accents (re-derived from the old sand tan).
      sand: {
        DEFAULT: '#E6E1D1',
        50: '#FAF8F3',
        100: '#F2EEE4',
        200: '#ECE7DA',
        light: '#EDE9DD',
      },
      // Crown alias for new/semantic usage.
      crown: {
        DEFAULT: '#E6E1D1',
        light: '#EDE9DD',
      },
      charcoal: {
        DEFAULT: '#2C2C2C',
        light: '#4A4A4A',
      },
      parchment: '#F5F2EA',
      // Saddle rust — the one accent, used for the primary action and for
      // "needs attention" signals only.
      saddle: {
        DEFAULT: '#A0522D',
        dark: '#86431F',
        50: '#FDF5F0',
        100: '#F5E0D3',
      },
      'light-sage': '#E4EAEB',
      'error-red': {
        DEFAULT: '#C0392B',
        50: '#FDF2F1',
        100: '#F5D5D2',
      },
      'info-blue': {
        DEFAULT: '#2E86AB',
        50: '#EFF7FA',
        100: '#D2EAF2',
      },
    },
    extend: {
      // Web fonts are self-hosted (static/fonts, declared in input.css). The
      // fallback faces are metric-matched to Arial so text does not reflow
      // when the real font arrives.
      fontFamily: {
        heading: ['"DM Sans"', '"DM Sans Fallback"', 'system-ui', 'sans-serif'],
        body: ['"Source Sans 3"', '"Source Sans 3 Fallback"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        btn: '6px',
        card: '12px',
        sm: '4px',
      },
      // Brim-tinted shadows: black shadows look muddy on the cream page.
      boxShadow: {
        card: '0 1px 2px rgba(44, 44, 44, 0.04), 0 4px 12px -4px rgba(61, 90, 99, 0.12)',
        float: '0 8px 24px -8px rgba(61, 90, 99, 0.25)',
      },
    },
  },
  plugins: [],
}
