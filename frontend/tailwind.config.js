/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#0a0e1a',
          900: '#0d1322',
          850: '#111931',
          800: '#16203d',
          700: '#1e2c52',
        },
        signal: {
          red: '#ff5c5c',
          amber: '#ffb454',
          green: '#3ddc97',
          blue: '#4d9fff',
          violet: '#a78bfa',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'Consolas', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
