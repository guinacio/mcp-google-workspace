/** Google Workspace-inspired color system, adapted to the host's dark and light modes. */
export const THEME_CSS = `
:root,
[data-theme="dark"] {
  color-scheme: dark;
  --md-sys-color-primary: var(--color-text-info, #8ab4f8);
  --md-sys-color-on-primary: var(--color-text-inverse, #ffffff);
  --md-sys-color-primary-container: var(--color-background-info, #174ea6);
  --md-sys-color-on-primary-container: var(--color-text-info, #d2e3fc);
  --md-sys-color-secondary: var(--color-text-secondary, #c4c7c5);
  --md-sys-color-on-secondary: var(--color-text-inverse, #202124);
  --md-sys-color-surface: var(--color-background-primary, #202124);
  --md-sys-color-surface-container: var(--color-background-secondary, #292a2d);
  --md-sys-color-surface-container-high: var(--color-background-tertiary, #303134);
  --md-sys-color-surface-container-highest: var(--color-background-tertiary, #3c4043);
  --md-sys-color-surface-variant: var(--color-background-tertiary, #3c4043);
  --md-sys-color-on-surface: var(--color-text-primary, #e8eaed);
  --md-sys-color-on-surface-variant: var(--color-text-secondary, #bdc1c6);
  --md-sys-color-outline: var(--color-border-primary, #9aa0a6);
  --md-sys-color-outline-variant: var(--color-border-secondary, #5f6368);
  --md-sys-color-error: var(--color-text-danger, #f28b82);
  --workspace-tint: var(--color-background-info, #263b5a);
  --workspace-header: var(--color-background-secondary, #292a2d);

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
  --md-sys-color-primary: var(--color-text-info, #1a73e8);
  --md-sys-color-on-primary: var(--color-text-inverse, #ffffff);
  --md-sys-color-primary-container: var(--color-background-info, #d2e3fc);
  --md-sys-color-on-primary-container: var(--color-text-info, #174ea6);
  --md-sys-color-secondary: var(--color-text-secondary, #5f6368);
  --md-sys-color-on-secondary: var(--color-text-inverse, #ffffff);
  --md-sys-color-surface: var(--color-background-primary, #f8fafd);
  --md-sys-color-surface-container: var(--color-background-secondary, #ffffff);
  --md-sys-color-surface-container-high: var(--color-background-tertiary, #f1f3f4);
  --md-sys-color-surface-container-highest: var(--color-background-tertiary, #e8eaed);
  --md-sys-color-surface-variant: var(--color-background-tertiary, #f1f3f4);
  --md-sys-color-on-surface: var(--color-text-primary, #202124);
  --md-sys-color-on-surface-variant: var(--color-text-secondary, #5f6368);
  --md-sys-color-outline: var(--color-border-primary, #80868b);
  --md-sys-color-outline-variant: var(--color-border-secondary, #dadce0);
  --md-sys-color-error: var(--color-text-danger, #d93025);
  --workspace-tint: var(--color-background-info, #e8f0fe);
  --workspace-header: var(--color-background-secondary, #ffffff);

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
  font-family: var(--font-sans, "Google Sans", "Roboto", "Segoe UI", system-ui, -apple-system, sans-serif);
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
