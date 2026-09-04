/**
 * Classify non-OK POST /api/incidents/stream responses that reject the start
 * before any incident_started SSE event.
 */

export type PreIncidentCode =
  | "session_live_incident_limit"
  | "rate_limit_exceeded"
  | "sandbox_busy"
  | "turnstile_verification_failed"
  | "unknown_preincident";

export type PreIncidentStartPlan = {
  kind: "pre_incident";
  status: number;
  code: PreIncidentCode;
  message: string;
  detail: string | null;
  /** Remount Turnstile for another attempt (never for session quota). */
  remountTurnstile: boolean;
  /** Disable Start while this session remains capped. */
  disableStart: boolean;
  /** Show Retry control (false for hard session quota). */
  showRetry: boolean;
  preserveSelectedScenario: boolean;
  enterFailedWorkspace: boolean;
};

export class PreIncidentStartError extends Error {
  readonly kind = "pre_incident" as const;
  readonly status: number;
  readonly code: PreIncidentCode;
  readonly detail: string | null;
  readonly remountTurnstile: boolean;
  readonly disableStart: boolean;
  readonly showRetry: boolean;

  constructor(plan: PreIncidentStartPlan) {
    super(plan.message);
    this.name = "PreIncidentStartError";
    this.status = plan.status;
    this.code = plan.code;
    this.detail = plan.detail;
    this.remountTurnstile = plan.remountTurnstile;
    this.disableStart = plan.disableStart;
    this.showRetry = plan.showRetry;
  }
}

export function isPreIncidentStartError(
  cause: unknown,
): cause is PreIncidentStartError {
  return cause instanceof PreIncidentStartError;
}

export function classifyPreIncidentRejection(input: {
  status: number;
  errorCode: string | null;
}): PreIncidentStartPlan {
  const code = normalizeErrorCode(input.status, input.errorCode);

  switch (code) {
    case "session_live_incident_limit":
      return {
        kind: "pre_incident",
        status: input.status,
        code,
        message: "Live demo limit reached for this session.",
        detail:
          "The shared live sandbox limits the number of incidents per session.",
        remountTurnstile: false,
        disableStart: true,
        showRetry: false,
        preserveSelectedScenario: true,
        enterFailedWorkspace: false,
      };
    case "sandbox_busy":
      return {
        kind: "pre_incident",
        status: input.status,
        code,
        message: "Live sandbox is busy. Try again shortly.",
        detail: null,
        remountTurnstile: true,
        disableStart: false,
        showRetry: true,
        preserveSelectedScenario: true,
        enterFailedWorkspace: false,
      };
    case "rate_limit_exceeded":
      return {
        kind: "pre_incident",
        status: input.status,
        code,
        message: "Too many requests. Wait a moment, then try again.",
        detail: null,
        remountTurnstile: true,
        disableStart: false,
        showRetry: true,
        preserveSelectedScenario: true,
        enterFailedWorkspace: false,
      };
    case "turnstile_verification_failed":
      return {
        kind: "pre_incident",
        status: input.status,
        code,
        message: "Cloudflare check failed. Complete a new check to continue.",
        detail: null,
        remountTurnstile: true,
        disableStart: false,
        showRetry: true,
        preserveSelectedScenario: true,
        enterFailedWorkspace: false,
      };
    default:
      return {
        kind: "pre_incident",
        status: input.status,
        code: "unknown_preincident",
        message: "Unable to start a live incident right now. Try again shortly.",
        detail: null,
        remountTurnstile: true,
        disableStart: false,
        showRetry: true,
        preserveSelectedScenario: true,
        enterFailedWorkspace: false,
      };
  }
}

function normalizeErrorCode(
  status: number,
  errorCode: string | null,
): PreIncidentCode {
  const code = (errorCode ?? "").trim();
  if (code === "session_live_incident_limit") {
    return "session_live_incident_limit";
  }
  if (code === "rate_limit_exceeded") {
    return "rate_limit_exceeded";
  }
  if (code === "sandbox_busy") {
    return "sandbox_busy";
  }
  if (code === "turnstile_verification_failed") {
    return "turnstile_verification_failed";
  }
  if (status === 403) {
    return "turnstile_verification_failed";
  }
  if (status === 409) {
    return "sandbox_busy";
  }
  if (status === 429) {
    // Prefer explicit backend code; unknown 429 stays generic pre-incident.
    return "unknown_preincident";
  }
  return "unknown_preincident";
}

/** Extract FastAPI-style `{ detail: { error } }` or `{ detail: "..." }` safely. */
export function extractBackendErrorCode(payload: unknown): string | null {
  if (!isRecord(payload)) {
    return null;
  }
  const detail = payload.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (isRecord(detail) && typeof detail.error === "string") {
    return detail.error;
  }
  if (typeof payload.error === "string") {
    return payload.error;
  }
  return null;
}

export async function preIncidentErrorFromResponse(
  response: Response,
): Promise<PreIncidentStartError> {
  let errorCode: string | null = null;
  try {
    const payload: unknown = await response.json();
    errorCode = extractBackendErrorCode(payload);
  } catch {
    errorCode = null;
  }
  const plan = classifyPreIncidentRejection({
    status: response.status,
    errorCode,
  });
  return new PreIncidentStartError(plan);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
