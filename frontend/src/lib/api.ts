import type {
  BaselineEvaluation,
  IncidentApprovalResponse,
  IncidentAudit,
  IncidentStartResponse,
  LiveProvenance,
  Scenario,
} from "@/lib/types";

function apiPath(path: string): string {
  if (path === "/health" || path === "/healthz") {
    return "/api/health";
  }
  if (path === "/ready") {
    return "/api/ready";
  }
  return path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiPath(path), {
      credentials: "same-origin",
      ...init,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch {
    throw new Error(
      "Unable to reach OpsPilot. Confirm the API is running and try again.",
    );
  }

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  return (await response.json()) as T;
}

async function readError(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      return body.detail;
    }
  } catch {
    // Fall through to the status message.
  }
  return `Request failed (${response.status})`;
}

export function getScenarios(): Promise<Scenario[]> {
  return request<Scenario[]>("/api/scenarios");
}

export function startIncident(
  scenarioId: string,
  turnstileToken?: string | null,
): Promise<IncidentStartResponse> {
  return request<IncidentStartResponse>("/api/incidents/start", {
    method: "POST",
    body: JSON.stringify(incidentStartPayload(scenarioId, turnstileToken)),
  });
}

export function incidentStartPayload(
  scenarioId: string,
  turnstileToken?: string | null,
): { scenario_id: string; turnstile_token?: string } {
  if (turnstileToken) {
    return { scenario_id: scenarioId, turnstile_token: turnstileToken };
  }
  return { scenario_id: scenarioId };
}

export function submitApproval(
  incidentId: string,
  approved: boolean,
): Promise<IncidentApprovalResponse> {
  return request<IncidentApprovalResponse>(
    `/api/incidents/${incidentId}/approval`,
    {
      method: "POST",
      body: JSON.stringify({ approved }),
    },
  );
}

export function getIncidentAudit(incidentId: string): Promise<IncidentAudit> {
  return request<IncidentAudit>(`/api/incidents/${incidentId}/audit`);
}

export function getBaselineEvaluation(): Promise<BaselineEvaluation> {
  return request<BaselineEvaluation>("/api/evaluations/baseline");
}

export type RuntimeSummary = {
  environment: string;
  deployment_profile?: string;
  model_provider: string;
  database: string;
  telemetry_mode: string;
  turnstile_site_key?: string;
};

export type SandboxStatus = {
  state: string;
  retry_after_seconds?: number;
};

export function getSandboxStatus(): Promise<SandboxStatus> {
  return request<SandboxStatus>("/api/sandbox/status");
}

export function getRuntimeSummary(): Promise<RuntimeSummary> {
  return request<RuntimeSummary>("/api/runtime");
}

export function getHealth(): Promise<{ status: string; service?: string }> {
  return request<{ status: string; service?: string }>("/api/health");
}

/** @deprecated Use getHealth(); kept name briefly for call-site clarity in cold-start. */
export function getHealthz(): Promise<{ status: string; service?: string }> {
  return getHealth();
}

export function getReadiness(): Promise<{
  status: string;
  degraded: boolean;
  checks?: Record<string, { ok: boolean; detail: string }>;
}> {
  return request("/api/ready");
}

export function getIncidentProvenance(incidentId: string): Promise<LiveProvenance> {
  return request<LiveProvenance>(`/api/incidents/${incidentId}/provenance`);
}
