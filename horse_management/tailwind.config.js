/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './horse_management/templates/**/*.html',
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
    extend: {
      colors: {
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
        saddle: {
          DEFAULT: '#A0522D',
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
      fontFamily: {
        heading: ['"DM Sans"', 'sans-serif'],
        body: ['"Source Sans 3"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      borderRadius: {
        btn: '6px',
        card: '8px',
        sm: '4px',
      },
    },
  },
  plugins: [],
}
