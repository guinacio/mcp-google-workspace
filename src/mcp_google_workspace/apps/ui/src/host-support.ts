/**
 * What the connected host actually supports, read after `ui/initialize`.
 *
 * Every optional host action is gated on this snapshot: a capability the host
 * did not declare is never attempted, and a request the host declines is a
 * denial the view reports (with a usable alternative), never a reason to force
 * a different path. `ui/download-file` is a draft-spec capability (not in the
 * stable 2026-01-26 Apps spec) and is treated as an optional enhancement.
 */
import type { McpUiHostCapabilities, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import type { UiHostFeatures } from "./types";

export interface HostSupport {
  /** Host proxies `tools/call` (and possibly `tools/list`) to the server. */
  serverTools: boolean;
  /** `ui/open-link`. */
  openLinks: boolean;
  /** Draft `ui/download-file`. */
  downloadFile: boolean;
  /** `ui/message` accepting text content. */
  message: boolean;
  /** `ui/update-model-context` accepting text content. */
  updateModelContext: boolean;
}

export const NO_HOST_SUPPORT: HostSupport = Object.freeze({
  serverTools: false,
  openLinks: false,
  downloadFile: false,
  message: false,
  updateModelContext: false,
});

export function readHostSupport(capabilities: McpUiHostCapabilities | undefined): HostSupport {
  if (!capabilities) return NO_HOST_SUPPORT;
  return {
    serverTools: !!capabilities.serverTools,
    openLinks: !!capabilities.openLinks,
    downloadFile: !!capabilities.downloadFile,
    message: !!capabilities.message?.text,
    updateModelContext: !!capabilities.updateModelContext?.text,
  };
}

export function hostFeatures(support: HostSupport, context: McpUiHostContext | undefined): UiHostFeatures {
  const modes = Array.isArray(context?.availableDisplayModes) ? context.availableDisplayModes : [];
  return {
    open_links: support.openLinks,
    download_files: support.downloadFile,
    send_messages: support.message,
    fullscreen_available: modes.includes("fullscreen"),
    display_mode: context?.displayMode,
  };
}

/** Maximum decoded attachment size the view hands to the host inline (base64 in postMessage). */
export const MAX_INLINE_DOWNLOAD_BYTES = 10 * 1024 * 1024;

/** Decoded byte length of a base64 string without decoding it. */
export function base64DecodedLength(value: string): number {
  const trimmed = value.replace(/\s+/g, "");
  const padding = trimmed.endsWith("==") ? 2 : trimmed.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((trimmed.length * 3) / 4) - padding);
}
