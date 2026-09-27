/**
 * Typed outcomes of server tool calls made through the MCP Apps host.
 *
 * A `callServerTool` promise can end three different ways and the dashboard
 * treats each explicitly, before any optimistic success message:
 *
 * - it resolves with `isError: true`: the tool ran and failed. The stable code is
 *   `structuredContent.code` (this server's error envelope);
 * - it resolves with a success payload that embeds an `error` object
 *   (`{"error": {"code": "PROVIDER_ERROR", ...}}`, the dashboard detail tools'
 *   AppError contract): also a failure;
 * - it rejects: a JSON-RPC error (`ProtocolError`, numeric code; this server puts
 *   its envelope, including the string `code`, in `error.data`), a local SDK error
 *   (`SdkError`: timeout, closed connection, unsupported result type such as an
 *   `input_required` round the host did not complete), a host refusal (for example
 *   a visibility rejection), or an abort after teardown.
 *
 * Codes are read from structured fields only; message text is never parsed.
 */
import { ProtocolError, SdkError } from "@modelcontextprotocol/client";

export type FailureKind = "tool" | "protocol" | "transport" | "cancelled";

/** A failed tool call with its classification and stable machine-readable code. */
export class ToolCallError extends Error {
  constructor(
    message: string,
    readonly kind: FailureKind,
    readonly code: string | null,
    readonly result?: unknown,
    readonly origin?: unknown,
  ) {
    super(message);
    this.name = "ToolCallError";
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/** Stable code of an `isError` result or of an embedded `error` payload. */
export function toolResultErrorCode(result: unknown): string | null {
  const structured = asRecord(asRecord(result)?.structuredContent);
  if (!structured) return null;
  if (typeof structured.code === "string") return structured.code;
  const embedded = asRecord(structured.error);
  return embedded && typeof embedded.code === "string" ? embedded.code : null;
}

/** Human-readable text of a failed result. */
export function toolResultErrorMessage(result: unknown): string {
  const candidate = asRecord(result);
  const structured = asRecord(candidate?.structuredContent);
  if (structured) {
    if (typeof structured.message === "string") return structured.message;
    const embedded = asRecord(structured.error);
    if (embedded && typeof embedded.message === "string") return embedded.message;
  }
  if (Array.isArray(candidate?.content)) {
    const text = (candidate.content as Array<{ type?: unknown; text?: unknown }>).find(
      (item) => item?.type === "text" && typeof item.text === "string",
    );
    if (text) return String(text.text);
  }
  return "The tool call failed.";
}

/** True for a success result whose payload is this server's `{error: {...}}` AppError. */
export function hasEmbeddedError(result: unknown): boolean {
  const structured = asRecord(asRecord(result)?.structuredContent);
  const embedded = asRecord(structured?.error);
  return !!embedded && typeof embedded.code === "string";
}

/** Classify a rejected call. */
export function classifyRejection(error: unknown, signal?: AbortSignal): ToolCallError {
  if (error instanceof ToolCallError) return error;
  if (signal?.aborted) {
    return new ToolCallError("The request was cancelled.", "cancelled", "cancelled", undefined, error);
  }
  if (ProtocolError.isInstance(error)) {
    const data = asRecord(error.data);
    const code = data && typeof data.code === "string" ? data.code : `jsonrpc_${error.code}`;
    const message = data && typeof data.message === "string" ? data.message : error.message;
    return new ToolCallError(message, "protocol", code, data ?? undefined, error);
  }
  if (SdkError.isInstance(error)) {
    return new ToolCallError(error.message, "transport", String(error.code), undefined, error);
  }
  const message = error instanceof Error ? error.message : String(error);
  return new ToolCallError(message || "The host rejected the request.", "transport", null, undefined, error);
}

/** Throw a classified error for any failed result; return successful ones unchanged. */
export function requireSuccess<T>(result: T): T {
  if (asRecord(result)?.isError === true) {
    throw new ToolCallError(toolResultErrorMessage(result), "tool", toolResultErrorCode(result), result);
  }
  if (hasEmbeddedError(result)) {
    throw new ToolCallError(toolResultErrorMessage(result), "tool", toolResultErrorCode(result), result);
  }
  return result;
}

/** JSON-RPC codes a host or server uses for an unknown tool name. */
const UNKNOWN_TOOL_RPC_CODES = new Set(["jsonrpc_-32601", "jsonrpc_-32602"]);

/** Whether trying the next guessed read-tool name is sensible after this failure. */
export function isUnknownToolError(error: ToolCallError): boolean {
  return error.kind === "protocol" && error.code !== null && UNKNOWN_TOOL_RPC_CODES.has(error.code);
}

const FRIENDLY_CODES: Record<string, string> = {
  confirmation_required: "The server needs your confirmation for this action. Ask the assistant in chat to do it.",
  prepare_required: "This action needs a preview before it can run. Ask the assistant in chat to do it.",
  input_required: "The server asked for more input that this view cannot provide. Ask the assistant in chat to do it.",
  UNSUPPORTED_RESULT_TYPE: "The server asked for more input that this view cannot provide. Ask the assistant in chat to do it.",
  REQUEST_TIMEOUT: "The host did not answer in time.",
  CONNECTION_CLOSED: "The connection to the host was closed.",
};

/** Message shown to the user for a failure. */
export function describeFailure(error: unknown): string {
  if (error instanceof ToolCallError) {
    const friendly = error.code ? FRIENDLY_CODES[error.code] : undefined;
    return friendly ?? error.message;
  }
  return error instanceof Error ? error.message : String(error);
}
