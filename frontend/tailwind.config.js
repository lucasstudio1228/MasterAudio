/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: '#0f1117',
        surface: '#1a1d2e',
        'surface-2': '#232741',
        accent: '#7c3aed',
        'accent-hover': '#6d28d9',
      },
    },
  },
  plugins: [],
};
