/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'quicksuite-purple': '#6B46C1',
        'quicksuite-blue': '#3B82F6',
        'aws-dark-blue': '#232F3E',
        'aws-gray': '#F2F3F3',
      },
      fontFamily: {
        'amazon': ['Amazon Ember', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
