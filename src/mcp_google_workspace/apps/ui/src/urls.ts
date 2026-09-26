/**
 * Scheme validation for URLs that leave the dashboard. Escaping keeps a URL inside its
 * attribute; this module decides whether the URL may be used at all.
 */
export type LinkKind = "attachment" | "conference" | "email";

const LINK_SCHEMES: Record<LinkKind, readonly string[]> = {
  // Calendar attachments are Drive/HTTPS resources; conference links are HTTPS joins.
  attachment: ["https:"],
  conference: ["https:"],
  // Links authored by an email sender.
  email: ["https:", "http:", "mailto:"],
};

// C0/C1 controls, space-likes that browsers strip or render invisibly, bidi overrides.
// Whitespace inside a scheme ("java\tscript:") is a classic filter bypass.
const OBFUSCATION_CHARS =
  /[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u115f\u1160\u180e\u200b-\u200f\u202a-\u202e\u2028\u2029\u2060-\u206f\u3164\ufeff\uffa0]/;
const EXPLICIT_SCHEME = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;
const MAX_URL_LENGTH = 8192;

export function isLinkKind(value: unknown): value is LinkKind {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(LINK_SCHEMES, value);
}

/**
 * Returns the normalized absolute URL when it uses a scheme allowed for `kind`, else null.
 * Relative, protocol-relative, credential-bearing and obfuscated URLs are rejected.
 */
export function safeExternalUrl(raw: unknown, kind: LinkKind): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed.length > MAX_URL_LENGTH) return null;
  if (OBFUSCATION_CHARS.test(trimmed) || trimmed.includes("\\")) return null;
  const scheme = EXPLICIT_SCHEME.exec(trimmed);
  if (!scheme) return null;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  const protocol = parsed.protocol.toLowerCase();
  if (protocol !== `${scheme[1].toLowerCase()}:`) return null;
  if (!LINK_SCHEMES[kind].includes(protocol)) return null;
  if (protocol === "http:" || protocol === "https:") {
    if (!parsed.hostname || parsed.username || parsed.password) return null;
  }
  return parsed.href;
}
