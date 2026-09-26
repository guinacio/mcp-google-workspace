import DOMPurify from "dompurify";
import { safeExternalUrl } from "./urls";

/**
 * Sender-authored email HTML is untrusted. DOMPurify parses it in an inert document and
 * applies a restrictive allowlist; the post-pass below then enforces dashboard policy
 * (blocked remote images, host-mediated links, inline style allowlist) before the nodes
 * are attached to the live document. No markup string is re-parsed after sanitizing.
 */
const ALLOWED_TAGS = [
  "a",
  "abbr",
  "b",
  "blockquote",
  "br",
  "code",
  "del",
  "div",
  "em",
  "figcaption",
  "figure",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "i",
  "img",
  "li",
  "ol",
  "p",
  "pre",
  "span",
  "strong",
  "sub",
  "sup",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "u",
  "ul",
];

// Sender classes, ids and data-* attributes are dropped: they could restyle dashboard
// chrome, clobber globals, or trigger the dashboard's delegated data-* click actions.
const ALLOWED_ATTR = ["href", "src", "alt", "title", "style", "colspan", "rowspan"];

// Disallowed tags are normally unwrapped (their text is kept). These are removed with
// their content, matching the previous sanitizer and DOMPurify's defaults.
const DROP_WITH_CONTENT = [
  "applet",
  "audio",
  "base",
  "button",
  "canvas",
  "embed",
  "form",
  "frame",
  "frameset",
  "head",
  "iframe",
  "input",
  "link",
  "math",
  "meta",
  "noembed",
  "noframes",
  "noscript",
  "object",
  "picture",
  "plaintext",
  "script",
  "select",
  "source",
  "style",
  "svg",
  "template",
  "textarea",
  "title",
  "video",
  "xmp",
];

// Inline data images render without a network request. SVG is excluded: raster types
// are sufficient for email and keep active image formats out of the message body.
const SAFE_DATA_IMAGE = /^data:image\/(?:png|gif|jpe?g|webp|bmp|avif);base64,[a-z0-9+/=\s]+$/i;

// Sender-defined foreground and background colors can be unreadable in the host theme,
// so message text always inherits the app's contrast-safe palette. None of these
// properties accepts an image or URL value.
const ALLOWED_STYLE_PROPERTIES = [
  "borderBottomColor",
  "borderBottomStyle",
  "borderBottomWidth",
  "borderCollapse",
  "borderColor",
  "borderLeftColor",
  "borderLeftStyle",
  "borderLeftWidth",
  "borderRadius",
  "borderRightColor",
  "borderRightStyle",
  "borderRightWidth",
  "borderSpacing",
  "borderTopColor",
  "borderTopStyle",
  "borderTopWidth",
  "borderWidth",
  "fontFamily",
  "fontSize",
  "fontStyle",
  "fontWeight",
  "lineHeight",
  "margin",
  "marginBottom",
  "marginLeft",
  "marginRight",
  "marginTop",
  "padding",
  "paddingBottom",
  "paddingLeft",
  "paddingRight",
  "paddingTop",
  "textAlign",
  "textDecoration",
  "verticalAlign",
  "whiteSpace",
] as const;

const UNSAFE_CSS_VALUE = /\\|url\s*\(|image(?:-set)?\s*\(|expression\s*\(|@import|javascript:|-moz-binding|behavior\s*:|[<>]/i;

let purifier: ReturnType<typeof DOMPurify> | undefined;
let styleProbeDocument: Document | undefined;

function getPurifier(): ReturnType<typeof DOMPurify> {
  // A private instance keeps this configuration isolated from any other DOMPurify user.
  purifier ??= DOMPurify(window);
  return purifier;
}

function sanitizeCssValue(value: string): string | null {
  const normalized = value.trim();
  if (!normalized || UNSAFE_CSS_VALUE.test(normalized)) return null;
  return normalized;
}

export function sanitizeInlineStyle(styleText: string | null | undefined): string {
  if (!styleText) return "";
  // Parse declarations in an inert document so nothing is resolved against the live page.
  styleProbeDocument ??= document.implementation.createHTMLDocument("");
  const probe = styleProbeDocument.createElement("div");
  probe.setAttribute("style", styleText);
  const declarations: string[] = [];
  for (const property of ALLOWED_STYLE_PROPERTIES) {
    const safeValue = sanitizeCssValue(probe.style[property]);
    if (!safeValue) continue;
    const cssProperty = property.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`);
    declarations.push(`${cssProperty}:${safeValue}`);
  }
  return declarations.join("; ");
}

function blockedImagePlaceholder(image: Element): Element | null {
  const label = (image.getAttribute("alt") || image.getAttribute("title") || "").trim();
  if (!label) return null;
  const placeholder = image.ownerDocument.createElement("div");
  placeholder.className = "email-image-blocked";
  placeholder.textContent = label;
  return placeholder;
}

function applyDashboardPolicy(fragment: DocumentFragment): void {
  for (const element of Array.from(fragment.querySelectorAll("*"))) {
    const tag = element.localName;

    const style = element.getAttribute("style");
    if (style !== null) {
      const safeStyle = sanitizeInlineStyle(style);
      if (safeStyle) element.setAttribute("style", safeStyle);
      else element.removeAttribute("style");
    }

    if (tag === "img") {
      const src = (element.getAttribute("src") || "").trim();
      if (SAFE_DATA_IMAGE.test(src)) {
        element.removeAttribute("style");
        element.setAttribute("class", "email-html-image");
        continue;
      }
      // Remote, cid: and any other sources stay blocked (tracking protection).
      const placeholder = blockedImagePlaceholder(element);
      if (placeholder) element.replaceWith(placeholder);
      else element.remove();
      continue;
    }
    element.removeAttribute("src");

    if (tag === "a") {
      const href = safeExternalUrl(element.getAttribute("href"), "email");
      if (href) {
        element.setAttribute("href", href);
        element.setAttribute("data-open-link", "email");
        element.setAttribute("rel", "noopener noreferrer nofollow");
        element.setAttribute("target", "_blank");
        element.setAttribute("referrerpolicy", "no-referrer");
      } else {
        element.removeAttribute("href");
      }
    } else {
      element.removeAttribute("href");
    }

    if (tag === "table") {
      const hasOwnHeaders = element.querySelector(
        ":scope > thead th, :scope > tr > th, :scope > tbody > tr > th"
      );
      element.setAttribute("class", hasOwnHeaders ? "email-data-table" : "email-layout-table");
    }

    for (const span of ["colspan", "rowspan"]) {
      const value = element.getAttribute(span);
      if (value === null) continue;
      if (!(tag === "td" || tag === "th") || !/^\d{1,3}$/.test(value.trim())) {
        element.removeAttribute(span);
      }
    }
  }
}

function emptyBody(): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const empty = document.createElement("p");
  empty.className = "email-body-empty";
  empty.textContent = "No body content.";
  fragment.append(empty);
  return fragment;
}

/** Sanitizes sender HTML into detached nodes ready to append to the email body. */
export function sanitizeEmailHtml(htmlBody: string): DocumentFragment {
  const fragment = getPurifier().sanitize(htmlBody, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ADD_FORBID_CONTENTS: DROP_WITH_CONTENT,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOW_UNKNOWN_PROTOCOLS: false,
    ALLOW_SELF_CLOSE_IN_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
    SAFE_FOR_XML: true,
    WHOLE_DOCUMENT: false,
    RETURN_DOM_FRAGMENT: true,
  });
  applyDashboardPolicy(fragment);
  const hasContent = Array.from(fragment.childNodes).some(
    (node) => node.nodeType !== Node.TEXT_NODE || (node.textContent || "").trim() !== ""
  );
  return hasContent ? fragment : emptyBody();
}
