import type {
  BaselineEvaluation,
  IncidentApprovalResponse,
  IncidentAudit,
  IncidentStartResponse,
  LiveProvenance,
  Scenario,
} from "@/lib/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      credentials: "include",
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
): Promise<IncidentStartResponse> {
  return request<IncidentStartResponse>("/api/incidents/start", {
    method: "POST",
    body: JSON.stringify({ scenario_id: scenarioId }),
  });
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

export function getHealthz(): Promise<{ status: string }> {
  return request<{ status: string }>("/healthz");
}

export function getReadiness(): Promise<{
  status: string;
  degraded: boolean;
  checks?: Record<string, { ok: boolean; detail: string }>;
}> {
  return request("/ready");
}

export function getIncidentProvenance(incidentId: string): Promise<LiveProvenance> {
  return request<LiveProvenance>(`/api/incidents/${incidentId}/provenance`);
}
