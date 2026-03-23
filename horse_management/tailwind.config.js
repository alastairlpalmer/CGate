/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './horse_management/templates/**/*.html',
  ],
  theme: {
    extend: {
      colors: {
        forest: {
          DEFAULT: '#1B3A2D',
          light: '#2A5A45',
        },
        sage: {
          DEFAULT: '#6B8F71',
          50: '#F0F4F0',
          100: '#DCE5DD',
          200: '#B8CBB9',
          300: '#93B096',
          400: '#7FA084',
          light: '#8BAF90',
        },
        sand: {
          DEFAULT: '#D4C5A9',
          50: '#F5F1E8',
          100: '#EBE4D3',
          200: '#DFD5BC',
          light: '#E2D6BE',
        },
        charcoal: {
          DEFAULT: '#2C2C2C',
          light: '#4A4A4A',
        },
        parchment: '#F7F5F0',
        saddle: {
          DEFAULT: '#A0522D',
          50: '#FDF5F0',
          100: '#F5E0D3',
        },
        'light-sage': '#E8EDE9',
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
