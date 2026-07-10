/** Google Workspace-inspired color system, adapted to the host's dark and light modes. */
export const THEME_CSS = `
:root,
[data-theme="dark"] {
  color-scheme: dark;
  --md-sys-color-primary: #8ab4f8;
  --md-sys-color-on-primary: #ffffff;
  --md-sys-color-primary-container: #174ea6;
  --md-sys-color-on-primary-container: #d2e3fc;
  --md-sys-color-secondary: #c4c7c5;
  --md-sys-color-on-secondary: #202124;
  --md-sys-color-surface: #202124;
  --md-sys-color-surface-container: #292a2d;
  --md-sys-color-surface-container-high: #303134;
  --md-sys-color-surface-container-highest: #3c4043;
  --md-sys-color-surface-variant: #3c4043;
  --md-sys-color-on-surface: #e8eaed;
  --md-sys-color-on-surface-variant: #bdc1c6;
  --md-sys-color-outline: #9aa0a6;
  --md-sys-color-outline-variant: #5f6368;
  --md-sys-color-error: #f28b82;
  --workspace-tint: #263b5a;
  --workspace-header: #292a2d;

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
  --md-sys-color-primary: #1a73e8;
  --md-sys-color-on-primary: #ffffff;
  --md-sys-color-primary-container: #d2e3fc;
  --md-sys-color-on-primary-container: #174ea6;
  --md-sys-color-secondary: #5f6368;
  --md-sys-color-on-secondary: #ffffff;
  --md-sys-color-surface: #f8fafd;
  --md-sys-color-surface-container: #ffffff;
  --md-sys-color-surface-container-high: #f1f3f4;
  --md-sys-color-surface-container-highest: #e8eaed;
  --md-sys-color-surface-variant: #f1f3f4;
  --md-sys-color-on-surface: #202124;
  --md-sys-color-on-surface-variant: #5f6368;
  --md-sys-color-outline: #80868b;
  --md-sys-color-outline-variant: #dadce0;
  --md-sys-color-error: #d93025;
  --workspace-tint: #e8f0fe;
  --workspace-header: #ffffff;

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
  font-family: "Google Sans", "Roboto", "Segoe UI", system-ui, -apple-system, sans-serif;
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
