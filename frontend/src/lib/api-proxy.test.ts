import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { test } from "node:test";

import {
  buildUpstreamRequestHeaders,
  buildUpstreamUrl,
  copyUpstreamResponseHeaders,
  mutationOriginAllowed,
  proxyErrorBody,
  proxyErrorStatus,
  ProxyConfigError,
  resolveApiTarget,
  rewriteSetCookie,
  upstreamPathFor,
} from "./api-proxy.ts";

test("maps catch-all incidents/start onto /api/incidents/start", () => {
  assert.equal(upstreamPathFor(["incidents", "start"]), "/api/incidents/start");
  const url = buildUpstreamUrl(
    resolveApiTarget("https://canary.example.run.app"),
    ["incidents", "start"],
    "",
  );
  assert.equal(url.href, "https://canary.example.run.app/api/incidents/start");
});

test("maps health and ready onto process-local backend paths", () => {
  assert.equal(upstreamPathFor(["health"]), "/health");
  assert.equal(upstreamPathFor(["ready"]), "/ready");
});

test("rejects open-proxy path injection", () => {
  assert.throws(() => upstreamPathFor(["..", "secrets"]), /invalid API path/);
  assert.throws(() => upstreamPathFor(["http://evil.test"]), /invalid API path/);
  assert.throws(() => upstreamPathFor(["incidents@evil"]), /invalid API path/);
});

test("rejects credentialed or non-http targets", () => {
  assert.throws(() => resolveApiTarget(""), /not configured/);
  assert.throws(() => resolveApiTarget("ftp://x"), /http or https/);
  assert.throws(
    () => resolveApiTarget("https://user:pass@example.com"),
    /credentials/,
  );
});

test("does not forward Host, Origin, or client X-Forwarded-For", () => {
  const request = new Request("https://opspilot-chi.vercel.app/api/incidents/start", {
    headers: {
      host: "opspilot-chi.vercel.app",
      origin: "https://evil.example",
      cookie: "opspilot_session=abc",
      accept: "application/json",
      "content-type": "application/json",
      "user-agent": "OpsPilotTest",
      "x-forwarded-for": "1.2.3.4",
      "x-vercel-forwarded-for": "9.9.9.9",
    },
  });
  const headers = buildUpstreamRequestHeaders(request);
  assert.equal(headers.get("host"), null);
  assert.equal(headers.get("origin"), null);
  assert.equal(headers.get("cookie"), "opspilot_session=abc");
  assert.equal(headers.get("x-forwarded-for"), "9.9.9.9");
});

test("requires Origin to match Host for mutations", () => {
  assert.equal(
    mutationOriginAllowed(
      "POST",
      "https://opspilot-chi.vercel.app",
      "opspilot-chi.vercel.app",
    ),
    true,
  );
  assert.equal(
    mutationOriginAllowed("POST", "https://evil.example", "opspilot-chi.vercel.app"),
    false,
  );
  assert.equal(mutationOriginAllowed("GET", null, "opspilot-chi.vercel.app"), true);
});

test("rewrites Cloud Run Domain and SameSite=None for first-party Vercel cookies", () => {
  const rewritten = rewriteSetCookie(
    "opspilot_session=abc; Domain=.run.app; Path=/; HttpOnly; Secure; SameSite=None",
  );
  assert.match(rewritten, /^opspilot_session=abc;/);
  assert.doesNotMatch(rewritten, /Domain=/i);
  assert.match(rewritten, /HttpOnly/i);
  assert.match(rewritten, /Secure/i);
  assert.match(rewritten, /SameSite=Lax/i);
  assert.match(rewritten, /Path=\//i);
});

test("preserves multiple Set-Cookie headers without comma-joining", () => {
  const from = new Headers();
  from.append("set-cookie", "a=1; Path=/; HttpOnly");
  from.append("set-cookie", "b=2; Path=/; Secure");
  const copied = copyUpstreamResponseHeaders(from);
  assert.deepEqual(copied.getSetCookie(), [
    "a=1; Path=/; HttpOnly; SameSite=Lax",
    "b=2; Path=/; Secure; SameSite=Lax",
  ]);
});

test("streams SSE bytes incrementally instead of after completion", async () => {
  const received: string[] = [];
  let firstChunkAt = 0;
  let finishedAt = 0;

  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    if (req.url === "/api/incidents/stream") {
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
      });
      res.write("data: one\n\n");
      setTimeout(() => {
        res.write("data: two\n\n");
        res.end();
      }, 80);
      return;
    }
    res.writeHead(404);
    res.end();
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const target = `http://127.0.0.1:${address.port}`;
  const upstream = buildUpstreamUrl(
    resolveApiTarget(target),
    ["incidents", "stream"],
    "",
  );

  try {
    const response = await fetch(upstream, { method: "POST", body: "{}" });
    assert.equal(response.status, 200);
    assert.ok(response.body);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        finishedAt = Date.now();
        break;
      }
      if (!firstChunkAt) {
        firstChunkAt = Date.now();
      }
      received.push(decoder.decode(value));
    }
    assert.ok(received.some((chunk) => chunk.includes("data: one")));
    assert.ok(firstChunkAt > 0);
    assert.ok(finishedAt - firstChunkAt >= 50);
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
});

test("preserves non-2xx upstream status", async () => {
  const server = createServer((_req: IncomingMessage, res: ServerResponse) => {
    res.writeHead(409, { "content-type": "application/json" });
    res.end(JSON.stringify({ detail: { error: "sandbox_busy" } }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  try {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/x`, {
      method: "POST",
      body: "{}",
    });
    assert.equal(response.status, 409);
    const body = (await response.json()) as { detail: { error: string } };
    assert.equal(body.detail.error, "sandbox_busy");
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
});

test("unavailable target is classified as a proxy error", () => {
  assert.equal(proxyErrorStatus(new ProxyConfigError("missing")), 503);
  assert.deepEqual(proxyErrorBody(new Error("ECONNREFUSED")), {
    error: "upstream_unavailable",
  });
});
