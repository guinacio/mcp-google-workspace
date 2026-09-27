/**
 * Which server tool implements each dashboard operation.
 *
 * The server's launch results carry an explicit operation manifest in
 * `_meta["mcp-google-workspace/operations"]` (see apps/operations.py): the
 * operations this principal may use right now, with exact tool names. The view
 * trusts only that manifest for anything that changes state. Without a manifest
 * (before the first launch result, or from a host that drops `_meta`) only read
 * operations are attempted, by a `tools/list`-discovered name or a known name.
 * The server re-authorizes every call regardless.
 */
import type { UiToolCapabilities } from "./types";

export const OPERATIONS_META_KEY = "mcp-google-workspace/operations";

export type ReadOperation =
  | "getDashboard"
  | "getWeeklyCalendar"
  | "getEventDetail"
  | "getEmailDetail"
  | "getEmailAttachment"
  | "listCalendars";

export type WriteOperation =
  | "patchState"
  | "nextRange"
  | "prevRange"
  | "today"
  | "respondToEvent"
  | "createEvent"
  | "updateEvent"
  | "deleteEvent"
  | "markEmailRead"
  | "markEmailUnread"
  | "moveEmail"
  | "deleteEmail"
  | "untrashEmail"
  | "markEmailSpam"
  | "markEmailNotSpam";

export type Operation = ReadOperation | WriteOperation;

/**
 * Read fallbacks only: root-composed name first, then the subserver-only name.
 * Writes have no fallback on purpose.
 */
const READ_FALLBACKS: Record<ReadOperation, readonly string[]> = {
  getDashboard: ["apps_get_dashboard", "get_dashboard"],
  getWeeklyCalendar: ["apps_get_weekly_calendar_view", "get_weekly_calendar_view"],
  getEventDetail: ["apps_get_event_detail", "get_event_detail"],
  getEmailDetail: ["apps_get_email_detail", "get_email_detail"],
  getEmailAttachment: ["apps_get_email_attachment", "get_email_attachment"],
  listCalendars: ["calendar_list_calendars"],
};

export function isReadOperation(operation: Operation): operation is ReadOperation {
  return Object.prototype.hasOwnProperty.call(READ_FALLBACKS, operation);
}

export interface ManifestEntry {
  tool: string;
  mutates: boolean;
}

/** Parse a result's operation manifest; null when absent or malformed. */
export function readOperationManifest(result: unknown): Map<string, ManifestEntry> | null {
  if (!result || typeof result !== "object") return null;
  const meta = (result as { _meta?: unknown })._meta;
  if (!meta || typeof meta !== "object") return null;
  const manifest = (meta as Record<string, unknown>)[OPERATIONS_META_KEY];
  if (!manifest || typeof manifest !== "object") return null;
  const { version, operations } = manifest as { version?: unknown; operations?: unknown };
  if (version !== 1 || !operations || typeof operations !== "object") return null;
  const parsed = new Map<string, ManifestEntry>();
  for (const [operation, entry] of Object.entries(operations as Record<string, unknown>)) {
    if (!entry || typeof entry !== "object") continue;
    const { tool, mutates } = entry as { tool?: unknown; mutates?: unknown };
    if (typeof tool !== "string" || !tool || typeof mutates !== "boolean") continue;
    parsed.set(operation, { tool, mutates });
  }
  return parsed;
}

export class OperationRegistry {
  private manifest: Map<string, ManifestEntry> | null = null;
  private discovered: ReadonlySet<string> | null = null;
  private learned = new Map<ReadOperation, string>();

  get hasManifest(): boolean {
    return this.manifest !== null;
  }

  /** Adopt the manifest carried by a result; returns whether one was present. */
  adoptResult(result: unknown): boolean {
    const manifest = readOperationManifest(result);
    if (!manifest) return false;
    this.manifest = manifest;
    return true;
  }

  /** Tool names seen in (possibly partial) `tools/list` pages. */
  setDiscovered(names: ReadonlySet<string>): void {
    this.discovered = names;
  }

  /** Remember which guessed read name worked. */
  learn(operation: ReadOperation, tool: string): void {
    this.learned.set(operation, tool);
  }

  /**
   * Tool names to try, in order. A manifest is authoritative when present;
   * otherwise writes resolve to nothing and reads to discovered/known names.
   */
  candidates(operation: Operation): string[] {
    if (this.manifest) {
      const entry = this.manifest.get(operation);
      return entry ? [entry.tool] : [];
    }
    if (!isReadOperation(operation)) return [];
    const learned = this.learned.get(operation);
    if (learned) return [learned];
    const known = READ_FALLBACKS[operation];
    if (this.discovered) {
      const match = known.find((name) => this.discovered!.has(name));
      if (match) return [match];
    }
    return [...known];
  }

  available(operation: Operation): boolean {
    return this.candidates(operation).length > 0;
  }

  capabilities(): UiToolCapabilities {
    const has = (operation: Operation) => this.available(operation);
    return {
      can_create_event: has("createEvent"),
      can_edit_event: has("updateEvent"),
      can_delete_event: has("deleteEvent"),
      can_rsvp: has("respondToEvent"),
      can_reschedule_event: has("updateEvent"),
      can_navigate: has("nextRange") && has("prevRange") && has("today"),
      can_toggle_weekend: has("patchState"),
      can_select_calendars: has("patchState") && has("listCalendars"),
      can_mark_email_read: has("markEmailRead"),
      can_mark_email_unread: has("markEmailUnread"),
      can_archive_email: has("moveEmail"),
      can_trash_email: has("deleteEmail"),
      can_untrash_email: has("untrashEmail"),
      can_mark_email_spam: has("markEmailSpam"),
      can_mark_email_not_spam: has("markEmailNotSpam"),
      can_open_event_detail: has("getEventDetail"),
      can_open_email_detail: has("getEmailDetail"),
      can_fetch_email_attachment: has("getEmailAttachment"),
    };
  }
}
