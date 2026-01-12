/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  // Note: Removed prefix 'vp-' as the codebase uses standard Tailwind classes.
  // The host app should handle CSS isolation via Module Federation's scoping.
  // If CSS collisions occur, add prefix back and update all class names in components.
  corePlugins: {
    // Disable preflight to avoid resetting host app styles
    preflight: false,
  },
  theme: {
    extend: {},
  },
  plugins: [],
}
