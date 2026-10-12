/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        vault: {
          50: '#eef7f4',
          100: '#d6ece3',
          500: '#1f8a70',
          600: '#17705b',
          700: '#125a49',
          900: '#0a3d32',
        },
      },
    },
  },
  plugins: [],
};
