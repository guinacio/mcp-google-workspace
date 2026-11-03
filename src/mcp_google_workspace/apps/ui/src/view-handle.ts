/**
 * Server-issued dashboard view handles.
 *
 * The view never mints its own identity. The server returns a view descriptor
 * `{ handle, revision, expires_at, ttl_seconds }` in every dashboard tool result,
 * canonically under `_meta["mcp-google-workspace/view"]` and mirrored as
 * `structuredContent.view`. The view remembers it only in memory, sends the
 * handle with every dashboard callback, and sends the last seen revision as
 * `expected_revision` with every state update so a stale write is rejected by
 * the server instead of overwriting newer state.
 */

export const VIEW_META_KEY = "mcp-google-workspace/view";
const VIEW_HANDLE_PATTERN = /^wsv_[A-Za-z0-9_-]{43}$/;

export const VIEW_HANDLE_INVALID = "view_handle_invalid";
export const VIEW_STATE_CONFLICT = "view_state_conflict";

export interface ViewDescriptor {
  handle: string;
  revision?: number;
}

export function isViewHandle(value: unknown): value is string {
  return typeof value === "string" && VIEW_HANDLE_PATTERN.test(value);
}

function descriptorFrom(value: unknown): ViewDescriptor | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { handle?: unknown; revision?: unknown };
  if (!isViewHandle(candidate.handle)) return null;
  const revision =
    typeof candidate.revision === "number" && Number.isInteger(candidate.revision) && candidate.revision > 0
      ? candidate.revision
      : undefined;
  return { handle: candidate.handle, revision };
}

/** Read the view descriptor from a tool result (`_meta` first, then structuredContent.view). */
export function readViewDescriptor(result: unknown): ViewDescriptor | null {
  if (!result || typeof result !== "object") return null;
  const candidate = result as { _meta?: unknown; structuredContent?: unknown };
  const meta =
    candidate._meta && typeof candidate._meta === "object"
      ? (candidate._meta as Record<string, unknown>)[VIEW_META_KEY]
      : undefined;
  const structured =
    candidate.structuredContent && typeof candidate.structuredContent === "object"
      ? (candidate.structuredContent as Record<string, unknown>).view
      : undefined;
  return descriptorFrom(meta) ?? descriptorFrom(structured);
}

/** The in-memory handle of this one view. Never persisted, never minted here. */
export class ViewSession {
  private descriptor: ViewDescriptor | undefined;

  get handle(): string | undefined {
    return this.descriptor?.handle;
  }

  get revision(): number | undefined {
    return this.descriptor?.revision;
  }

  /**
   * Adopt a handle announced in the tool input (revision unknown until a result
   * arrives). Returns whether this view now uses that input's handle.
   */
  adoptInputHandle(value: unknown): boolean {
    if (!isViewHandle(value)) return false;
    if (!this.descriptor) this.descriptor = { handle: value };
    return this.descriptor.handle === value;
  }

  /** Adopt the descriptor carried by a server result; returns whether one was present. */
  adoptResult(result: unknown): boolean {
    const next = readViewDescriptor(result);
    if (!next) return false;
    if (
      next.handle === this.descriptor?.handle &&
      next.revision !== undefined &&
      this.descriptor.revision !== undefined &&
      next.revision < this.descriptor.revision
    ) {
      // A late response from an older revision of the same view.
      return true;
    }
    this.descriptor = next;
    return true;
  }

  forget(): void {
    this.descriptor = undefined;
  }
}
