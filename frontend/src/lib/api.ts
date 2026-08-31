import type {
  IncidentApprovalResponse,
  IncidentStartResponse,
  Scenario,
} from "@/lib/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
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
