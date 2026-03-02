/** Dark-first theme with teal accents, refined with MD3-style token structure. */
export const THEME_CSS = `
:root,
[data-theme="dark"] {
  color-scheme: dark;
  --md-sys-color-primary: #5ba4b5;
  --md-sys-color-on-primary: #ffffff;
  --md-sys-color-primary-container: #1a3a42;
  --md-sys-color-on-primary-container: #a8d8e4;
  --md-sys-color-secondary: #a8a5a0;
  --md-sys-color-on-secondary: #1a1a1a;
  --md-sys-color-surface: #0a0a0c;
  --md-sys-color-surface-container: #111114;
  --md-sys-color-surface-container-high: #1a1a1f;
  --md-sys-color-surface-variant: #18181c;
  --md-sys-color-on-surface: #e8e6e3;
  --md-sys-color-on-surface-variant: #a8a5a0;
  --md-sys-color-outline: #6b6966;
  --md-sys-color-outline-variant: #2a2a2f;
  --md-sys-color-error: #c45a5a;

  --accent-red: #c45a5a;
  --accent-amber: #d4a054;
  --accent-green: #7aad7a;

  --event-tomato: #f28b82;
  --event-flamingo: #f6aea9;
  --event-tangerine: #fdd663;
  --event-sage: #57bb8a;
  --event-basil: #43a047;
  --event-peacock: #4fc3f7;
  --event-blueberry: #9aa0ff;
  --event-lavender: #b39ddb;
  --event-grape: #c58af9;
  --event-graphite: #b0bec5;

  --md-sys-elevation-1: 0 2px 8px rgba(0, 0, 0, 0.35);
  --md-sys-elevation-2: 0 4px 14px rgba(0, 0, 0, 0.45);
  --md-sys-elevation-3: 0 6px 20px rgba(0, 0, 0, 0.5);

  --radius-xs: 6px;
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 20px;
}

[data-theme="light"] {
  color-scheme: light;
  --md-sys-color-primary: #3a8a9b;
  --md-sys-color-on-primary: #ffffff;
  --md-sys-color-primary-container: #d2eef5;
  --md-sys-color-on-primary-container: #0b5c6b;
  --md-sys-color-secondary: #555555;
  --md-sys-color-on-secondary: #ffffff;
  --md-sys-color-surface: #fafaf9;
  --md-sys-color-surface-container: #f0efed;
  --md-sys-color-surface-container-high: #e8e7e5;
  --md-sys-color-surface-variant: #e0dfdd;
  --md-sys-color-on-surface: #1a1a1a;
  --md-sys-color-on-surface-variant: #555555;
  --md-sys-color-outline: #888888;
  --md-sys-color-outline-variant: #d0cfcd;
  --md-sys-color-error: #b04040;

  --accent-red: #b04040;
  --accent-amber: #b88030;
  --accent-green: #4a8a4a;

  --event-tomato: #d93025;
  --event-flamingo: #e67c73;
  --event-tangerine: #f6bf26;
  --event-sage: #33b679;
  --event-basil: #0b8043;
  --event-peacock: #039be5;
  --event-blueberry: #3f51b5;
  --event-lavender: #7986cb;
  --event-grape: #8e24aa;
  --event-graphite: #616161;

  --md-sys-elevation-1: 0 2px 8px rgba(0, 0, 0, 0.08);
  --md-sys-elevation-2: 0 4px 14px rgba(0, 0, 0, 0.12);
  --md-sys-elevation-3: 0 6px 20px rgba(0, 0, 0, 0.15);
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  padding: 0;
}

body {
  background: var(--md-sys-color-surface);
  color: var(--md-sys-color-on-surface);
  font-family: 'DM Sans', 'Segoe UI', system-ui, -apple-system, sans-serif;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  overflow-x: auto;
}

button,
input,
textarea,
select {
  font: inherit;
}
`;

export function applyTheme(theme: "dark" | "light") {
  document.documentElement.setAttribute("data-theme", theme);
}
