import type { Config } from "tailwindcss";

// "Department store" theme: deep teal brand, warm gold for the main buy action, cool-neutral greys.
// `slate` is re-tuned so every existing page picks up the same neutrals without per-file edits.
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"] },
      colors: {
        brand: { 50: "#eef5f5", 100: "#d6e6e6", 200: "#b3cfd0", 500: "#14707a", 600: "#0e4f55", 700: "#0b3f44", 900: "#072a2d" },
        gold: { 50: "#fdf6ea", 100: "#fcefd9", 500: "#d99a1e", 600: "#c2861a", 800: "#8a5a0c" },
        good: { 50: "#e3f2ea", 600: "#1b6b43", 700: "#155535" },
        alert: { 50: "#fbeae5", 600: "#b23a1f" },
        // Stock palettes used by older pages, retuned to the theme (good = green, amber = gold, rose = brick).
        emerald: { 50: "#e3f2ea", 100: "#cde8d8", 200: "#a8d5bb", 500: "#2e8b57", 600: "#1b6b43", 700: "#1b6b43", 800: "#155535" },
        amber: { 50: "#fdf6ea", 100: "#fcefd9", 200: "#f3d9a6", 500: "#d99a1e", 700: "#9a650f", 800: "#8a5a0c", 900: "#5e3d08" },
        rose: { 50: "#fbeae5", 200: "#f0c2b5", 500: "#c4492b", 700: "#b23a1f", 800: "#962f18", 900: "#6e2211" },
        sky: { 50: "#eef5f5", 200: "#b3cfd0", 700: "#0e4f55", 800: "#0b3f44" },
        teal: { 50: "#eef5f5", 200: "#b3cfd0", 700: "#0e4f55", 800: "#0b3f44" },
        violet: { 50: "#eef1f1", 200: "#cfd6d7", 700: "#3b4447", 800: "#283033" },
        slate: {
          50: "#f6f7f6", 100: "#eef1f1", 200: "#e1e6e6", 300: "#cfd6d7", 400: "#9aa6a8",
          500: "#6b7679", 600: "#4f5a5d", 700: "#3b4447", 800: "#283033", 900: "#1b2124",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
