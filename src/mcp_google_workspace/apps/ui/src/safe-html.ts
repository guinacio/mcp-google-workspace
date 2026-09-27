/**
 * Auto-escaping HTML templates for dashboard chrome.
 *
 * Every `${value}` interpolated into an `html` template is HTML-escaped unless it is
 * itself a `SafeHtml` produced by another `html` template. The escaper covers
 * `& < > " ' \``, so an escaped value is inert in element text, RCDATA (`<textarea>`) and
 * quoted attribute values. Templates must therefore only interpolate into those
 * contexts: never into tag or attribute names, unquoted attributes, `on*` handlers,
 * `<script>`/`<style>` bodies, or URL-bearing attributes without `safeExternalUrl`.
 *
 * The brand is a module-private symbol, so JSON from a server or host cannot forge
 * trusted markup.
 */
const SAFE_HTML = Symbol("safe-html");

export interface SafeHtml {
  readonly [SAFE_HTML]: string;
}

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
  "`": "&#96;",
};

export function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>"'`]/g, (ch) => HTML_ESCAPES[ch]);
}

function isSafeHtml(value: unknown): value is SafeHtml {
  return typeof value === "object" && value !== null && SAFE_HTML in value;
}

function interpolate(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (isSafeHtml(value)) return value[SAFE_HTML];
  if (Array.isArray(value)) return value.map(interpolate).join("");
  return escapeHtml(value);
}

export function html(strings: TemplateStringsArray, ...values: unknown[]): SafeHtml {
  let out = strings[0];
  for (let i = 0; i < values.length; i += 1) {
    out += interpolate(values[i]) + strings[i + 1];
  }
  return { [SAFE_HTML]: out };
}

/** Escaped text in which line breaks become `<br />`. */
export function textWithBreaks(text: string): SafeHtml {
  return { [SAFE_HTML]: escapeHtml(text).replace(/\n/g, "<br />") };
}

export function setHtml(element: Element, content: SafeHtml): void {
  element.innerHTML = content[SAFE_HTML];
}
