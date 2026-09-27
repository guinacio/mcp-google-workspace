/**
 * Different-origin MCP Apps sandbox proxy for the browser tests.
 *
 * The test host page runs on http://127.0.0.1:4173 (Vite). This server runs on
 * http://localhost:4174, a different origin, and plays the web-host sandbox
 * proxy of the stable Apps spec (2026-01-26):
 *
 * 1. `/sandbox.html` is the proxy document. The host embeds it in an iframe with
 *    `sandbox="allow-scripts allow-same-origin"`. It announces
 *    `ui/notifications/sandbox-proxy-ready` to the host origin only.
 * 2. The host answers `ui/notifications/sandbox-resource-ready` with the raw UI
 *    HTML and the resource's `_meta.ui.csp`. The proxy registers them here and
 *    loads `/view/<id>` in an inner iframe with `sandbox="allow-scripts allow-forms"`
 *    (opaque origin: no same-origin access, no popups, no top navigation).
 * 3. `/view/<id>` is served with a `Content-Security-Policy` response header built
 *    from the declared CSP exactly as the spec prescribes; with nothing declared
 *    that is the spec's restrictive default.
 * 4. The proxy relays every other message between host and view, checking the
 *    host's origin and the view's window.
 *
 * `/probe/*` records hits so tests can prove CSP-blocked requests never left the
 * browser; `/probe-log` returns them.
 *
 * Run with Node 24 (built-in TypeScript type stripping): `node tests/sandbox-server.ts`.
 */
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.SANDBOX_PORT ?? 4174);
const HOST = process.env.SANDBOX_HOST ?? "localhost";
const HOST_ORIGIN = process.env.SANDBOX_ALLOWED_HOST_ORIGIN ?? "http://127.0.0.1:4173";
const MAX_RESOURCE_BYTES = 24 * 1024 * 1024;

interface DeclaredCsp {
  connectDomains?: string[];
  resourceDomains?: string[];
  frameDomains?: string[];
  baseUriDomains?: string[];
}

const views = new Map<string, { html: string; csp: string }>();
const probes: string[] = [];

/** Only absolute http(s) origins (optionally with a leading `*.` wildcard) become CSP sources. */
function sources(domains: unknown): string[] {
  if (!Array.isArray(domains)) return [];
  return domains.filter(
    (value): value is string => typeof value === "string" && /^https?:\/\/(\*\.)?[A-Za-z0-9.-]+(:\d+)?$/.test(value),
  );
}

/** The spec's CSP: restrictive default plus exactly the declared domains. */
export function buildViewCsp(declared: DeclaredCsp | undefined): string {
  const resource = sources(declared?.resourceDomains);
  const connect = sources(declared?.connectDomains);
  const frame = sources(declared?.frameDomains);
  const baseUri = sources(declared?.baseUriDomains);
  const list = (base: string[], extra: string[]) => [...base, ...extra].join(" ");
  const directives = [
    "default-src 'none'",
    `script-src ${list(["'self'", "'unsafe-inline'"], resource)}`,
    `style-src ${list(["'self'", "'unsafe-inline'"], resource)}`,
    `img-src ${list(["'self'", "data:"], resource)}`,
    `media-src ${list(["'self'", "data:"], resource)}`,
    `connect-src ${connect.length ? connect.join(" ") : "'none'"}`,
  ];
  if (resource.length) directives.push(`font-src ${list(["'self'"], resource)}`);
  if (frame.length) directives.push(`frame-src ${frame.join(" ")}`);
  if (baseUri.length) directives.push(`base-uri ${baseUri.join(" ")}`);
  return directives.join("; ");
}

const PROXY_HTML = `<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>MCP Apps sandbox proxy</title>
<style>html,body{margin:0;height:100%;overflow:hidden}iframe{border:0;width:100%;height:100%;display:block}</style>
</head>
<body>
<script>
(() => {
  const HOST_ORIGIN = ${JSON.stringify(HOST_ORIGIN)};
  let inner = null;
  window.addEventListener("message", async (event) => {
    const message = event.data;
    if (event.source === window.parent) {
      if (event.origin !== HOST_ORIGIN) return;
      const method = message && typeof message.method === "string" ? message.method : "";
      if (method === "ui/notifications/sandbox-resource-ready") {
        const params = message.params || {};
        const response = await fetch("/register", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ html: params.html, csp: params.csp }),
        });
        const { id } = await response.json();
        inner = document.createElement("iframe");
        inner.id = "view";
        inner.title = "MCP App view";
        inner.setAttribute("sandbox", typeof params.sandbox === "string" ? params.sandbox : "allow-scripts allow-forms");
        inner.src = "/view/" + encodeURIComponent(id);
        document.body.appendChild(inner);
        return;
      }
      if (method.startsWith("ui/notifications/sandbox-")) return;
      if (inner && inner.contentWindow) inner.contentWindow.postMessage(message, "*");
      return;
    }
    if (inner && event.source === inner.contentWindow) {
      window.parent.postMessage(message, HOST_ORIGIN);
    }
  });
  window.parent.postMessage({ jsonrpc: "2.0", method: "ui/notifications/sandbox-proxy-ready", params: {} }, HOST_ORIGIN);
})();
</script>
</body>
</html>`;

const PROXY_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src 'self'";

function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_RESOURCE_BYTES) {
        reject(new Error("resource too large"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function send(response: ServerResponse, status: number, body: string, headers: Record<string, string> = {}) {
  response.writeHead(status, { "cache-control": "no-store", ...headers });
  response.end(body);
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${HOST}:${PORT}`);
  try {
    if (request.method === "GET" && url.pathname === "/") {
      send(response, 200, "sandbox ok", { "content-type": "text/plain" });
      return;
    }
    if (request.method === "GET" && url.pathname === "/sandbox.html") {
      send(response, 200, PROXY_HTML, {
        "content-type": "text/html; charset=utf-8",
        "content-security-policy": PROXY_CSP,
      });
      return;
    }
    if (request.method === "POST" && url.pathname === "/register") {
      const payload = JSON.parse(await readBody(request)) as { html?: unknown; csp?: DeclaredCsp };
      if (typeof payload.html !== "string") {
        send(response, 400, "html required");
        return;
      }
      const id = randomUUID();
      views.set(id, { html: payload.html, csp: buildViewCsp(payload.csp) });
      send(response, 200, JSON.stringify({ id }), { "content-type": "application/json" });
      return;
    }
    if (request.method === "GET" && url.pathname.startsWith("/view/")) {
      const view = views.get(decodeURIComponent(url.pathname.slice("/view/".length)));
      if (!view) {
        send(response, 404, "unknown view");
        return;
      }
      send(response, 200, view.html, {
        "content-type": "text/html; charset=utf-8",
        "content-security-policy": view.csp,
        "x-content-type-options": "nosniff",
      });
      return;
    }
    if (url.pathname.startsWith("/probe/")) {
      probes.push(url.pathname);
      send(response, 204, "");
      return;
    }
    if (request.method === "GET" && url.pathname === "/probe-log") {
      send(response, 200, JSON.stringify(probes), {
        "content-type": "application/json",
        "access-control-allow-origin": HOST_ORIGIN,
      });
      return;
    }
    send(response, 404, "not found");
  } catch (error) {
    send(response, 500, String(error));
  }
});

server.listen(PORT, HOST, () => {
  console.log(`MCP Apps sandbox proxy on http://${HOST}:${PORT} (host origin ${HOST_ORIGIN})`);
});
