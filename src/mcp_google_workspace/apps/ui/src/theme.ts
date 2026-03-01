/** CSS custom properties matching Sentinel's palette. Dark default, light via data-theme="light". */
export const THEME_CSS = `
:root,
[data-theme="dark"] {
  --bg-primary:      #0a0a0c;
  --bg-secondary:    #111114;
  --bg-overlay:      #18181c;
  --bg-card:         #1a1a1f;
  --fg-primary:      #e8e6e3;
  --fg-secondary:    #a8a5a0;
  --fg-muted:        #6b6966;
  --accent-primary:  #5ba4b5;
  --accent-teal:     #5ba4b5;
  --accent-red:      #c45a5a;
  --accent-amber:    #d4a054;
  --accent-green:    #7aad7a;
  --border-subtle:   #252528;
  --border-medium:   #333338;
  --radius-sm:       4px;
  --radius-md:       8px;
  --radius-lg:       12px;
  --shadow-card:     0 2px 8px rgba(0,0,0,0.3);
}

[data-theme="light"] {
  --bg-primary:      #fafaf9;
  --bg-secondary:    #f0efed;
  --bg-overlay:      #e8e7e5;
  --bg-card:         #ffffff;
  --fg-primary:      #1a1a1a;
  --fg-secondary:    #555555;
  --fg-muted:        #888888;
  --accent-primary:  #3a8a9b;
  --accent-teal:     #3a8a9b;
  --accent-red:      #b04040;
  --accent-amber:    #b88030;
  --accent-green:    #4a8a4a;
  --border-subtle:   #e0dfdd;
  --border-medium:   #d0cfcd;
  --shadow-card:     0 2px 8px rgba(0,0,0,0.08);
}

*,
*::before,
*::after {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'DM Sans', system-ui, -apple-system, sans-serif;
  background: var(--bg-primary);
  color: var(--fg-primary);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  overflow-x: hidden;
}

.font-display {
  font-family: 'Cormorant Garamond', 'Georgia', serif;
}

.font-mono {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
}
`;

export function applyTheme(theme: "dark" | "light") {
  document.documentElement.setAttribute("data-theme", theme);
}
