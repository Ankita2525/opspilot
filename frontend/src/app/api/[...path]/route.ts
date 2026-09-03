import { type NextRequest, NextResponse } from "next/server";

import {
  buildUpstreamRequestHeaders,
  buildUpstreamUrl,
  copyUpstreamResponseHeaders,
  mutationOriginAllowed,
  proxyErrorBody,
  proxyErrorStatus,
  resolveApiTarget,
} from "@/lib/api-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

function jsonError(error: unknown): NextResponse {
  return NextResponse.json(proxyErrorBody(error), {
    status: proxyErrorStatus(error),
  });
}

async function proxy(
  request: NextRequest,
  context: RouteContext,
): Promise<Response> {
  try {
    if (
      !mutationOriginAllowed(
        request.method,
        request.headers.get("origin"),
        request.headers.get("host"),
      )
    ) {
      return NextResponse.json(
        { error: "csrf_origin_mismatch" },
        { status: 403 },
      );
    }

    const { path } = await context.params;
    const target = resolveApiTarget(process.env.OPSPILOT_API_TARGET);
    const upstreamUrl = buildUpstreamUrl(
      target,
      path,
      request.nextUrl.search,
    );
    const method = request.method.toUpperCase();
    const headers = buildUpstreamRequestHeaders(request);
    const init: RequestInit & { duplex?: "half" } = {
      method,
      headers,
      redirect: "manual",
      cache: "no-store",
      signal: request.signal,
    };
    if (method !== "GET" && method !== "HEAD") {
      init.body = request.body;
      init.duplex = "half";
    }

    const upstream = await fetch(upstreamUrl, init);
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: copyUpstreamResponseHeaders(upstream.headers),
    });
  } catch (error) {
    return jsonError(error);
  }
}

export function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export function PUT(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export function PATCH(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export function DELETE(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export function HEAD(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
