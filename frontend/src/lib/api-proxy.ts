const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "content-length",
  "host",
]);

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "cookie",
  "user-agent",
] as const;

const BLOCKED_RESPONSE_HEADERS = new Set([
  ...HOP_BY_HOP,
  "access-control-allow-origin",
  "access-control-allow-credentials",
  "access-control-allow-headers",
  "access-control-allow-methods",
  "access-control-expose-headers",
  "access-control-max-age",
]);

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ProxyConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProxyConfigError";
  }
}

export class ProxyRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProxyRequestError";
  }
}

export function resolveApiTarget(raw: string | undefined): URL {
  const value = raw?.trim() ?? "";
  if (!value) {
    throw new ProxyConfigError("OPSPILOT_API_TARGET is not configured");
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new ProxyConfigError("OPSPILOT_API_TARGET is not a valid URL");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new ProxyConfigError("OPSPILOT_API_TARGET must be http or https");
  }
  if (parsed.username || parsed.password) {
    throw new ProxyConfigError("OPSPILOT_API_TARGET must not include credentials");
  }
  if (parsed.search || parsed.hash) {
    throw new ProxyConfigError("OPSPILOT_API_TARGET must not include query or fragment");
  }
  return parsed;
}

export function assertSafePathSegments(segments: string[]): void {
  if (segments.length === 0) {
    throw new ProxyRequestError("missing API path");
  }
  for (const segment of segments) {
    if (!segment || segment === "." || segment === "..") {
      throw new ProxyRequestError("invalid API path");
    }
    if (
      segment.includes("/") ||
      segment.includes("\\") ||
      segment.includes("://") ||
      segment.includes("@") ||
      segment.includes("..")
    ) {
      throw new ProxyRequestError("invalid API path");
    }
  }
}

export function upstreamPathFor(segments: string[]): string {
  assertSafePathSegments(segments);
  if (segments.length === 1 && segments[0] === "health") {
    return "/health";
  }
  if (segments.length === 1 && segments[0] === "ready") {
    return "/ready";
  }
  return `/api/${segments.map(encodeURIComponent).join("/")}`;
}

export function buildUpstreamUrl(
  target: URL,
  segments: string[],
  search: string,
): URL {
  const url = new URL(upstreamPathFor(segments), target);
  url.search = search.startsWith("?") ? search.slice(1) : search;
  if (url.origin !== target.origin) {
    throw new ProxyRequestError("refusing to proxy off-target origin");
  }
  return url;
}

export function mutationOriginAllowed(
  method: string,
  originHeader: string | null,
  hostHeader: string | null,
): boolean {
  if (!MUTATING_METHODS.has(method.toUpperCase())) {
    return true;
  }
  if (!originHeader || !hostHeader) {
    return false;
  }
  try {
    const origin = new URL(originHeader);
    return origin.host === hostHeader;
  } catch {
    return false;
  }
}

export function selectClientIp(headers: Headers): string | null {
  const vercel = headers.get("x-vercel-forwarded-for");
  if (vercel) {
    return vercel.split(",")[0]?.trim() || null;
  }
  const realIp = headers.get("x-real-ip");
  if (realIp) {
    return realIp.split(",")[0]?.trim() || null;
  }
  return null;
}

export function buildUpstreamRequestHeaders(request: Request): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }
  const clientIp = selectClientIp(request.headers);
  if (clientIp) {
    headers.set("x-forwarded-for", clientIp);
  }
  return headers;
}

export function rewriteSetCookie(raw: string): string {
  const parts = raw.split(";");
  const nameValue = parts[0]?.trim();
  if (!nameValue) {
    return raw;
  }
  const attributes: string[] = [];
  let sameSite: string | null = null;
  let pathSeen = false;
  for (const part of parts.slice(1)) {
    const trimmed = part.trim();
    if (!trimmed) {
      continue;
    }
    const eq = trimmed.indexOf("=");
    const key = (eq === -1 ? trimmed : trimmed.slice(0, eq)).trim().toLowerCase();
    const value = eq === -1 ? "" : trimmed.slice(eq + 1).trim();
    if (key === "domain") {
      const domain = value.replace(/^\./, "").toLowerCase();
      if (domain.endsWith("run.app") || domain.endsWith("vercel.app")) {
        continue;
      }
      attributes.push(trimmed);
      continue;
    }
    if (key === "samesite") {
      sameSite = value.toLowerCase() === "none" ? "Lax" : value;
      continue;
    }
    if (key === "path") {
      pathSeen = true;
    }
    attributes.push(trimmed);
  }
  if (!pathSeen) {
    attributes.push("Path=/");
  }
  if (sameSite) {
    attributes.push(`SameSite=${sameSite}`);
  } else {
    attributes.push("SameSite=Lax");
  }
  return [nameValue, ...attributes].join("; ");
}

export function copyUpstreamResponseHeaders(from: Headers): Headers {
  const headers = new Headers();
  from.forEach((value, key) => {
    if (BLOCKED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      return;
    }
    if (key.toLowerCase() === "set-cookie") {
      return;
    }
    headers.set(key, value);
  });
  const cookies =
    typeof from.getSetCookie === "function" ? from.getSetCookie() : [];
  for (const cookie of cookies) {
    headers.append("set-cookie", rewriteSetCookie(cookie));
  }
  const contentType = headers.get("content-type") ?? "";
  if (contentType.includes("text/event-stream")) {
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  }
  return headers;
}

export function proxyErrorStatus(error: unknown): number {
  if (error instanceof ProxyConfigError) {
    return 503;
  }
  if (error instanceof ProxyRequestError) {
    return 400;
  }
  return 502;
}

export function proxyErrorBody(error: unknown): { error: string } {
  if (error instanceof ProxyConfigError) {
    return { error: "api_target_unconfigured" };
  }
  if (error instanceof ProxyRequestError) {
    return { error: "invalid_proxy_request" };
  }
  return { error: "upstream_unavailable" };
}
