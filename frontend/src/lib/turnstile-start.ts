/**
 * Pre-incident Turnstile gating for live starts.
 * Tokens remain browser-generated, single-use, and never reused.
 */

export type StartRetryPlan = {
  /** Never stream immediately on Retry — require a fresh challenge first. */
  nextAction: "return_to_start";
  clearFailedWorkspace: boolean;
  clearLiveIncidentState: boolean;
  clearProvenance: boolean;
  remountTurnstile: boolean;
  clearTurnstileToken: boolean;
  preserveSelectedScenario: boolean;
  callStream: boolean;
};

export function canStartLiveIncident(input: {
  turnstileRequired: boolean;
  turnstileToken: string | null | undefined;
}): boolean {
  if (!input.turnstileRequired) {
    return true;
  }
  return Boolean(input.turnstileToken);
}

/**
 * Retry after a failed Start must not POST /api/incidents/stream until the
 * user completes a fresh Turnstile challenge.
 */
export function planStartRetry(): StartRetryPlan {
  return {
    nextAction: "return_to_start",
    clearFailedWorkspace: true,
    clearLiveIncidentState: true,
    clearProvenance: true,
    remountTurnstile: true,
    clearTurnstileToken: true,
    preserveSelectedScenario: true,
    callStream: false,
  };
}

/**
 * Simulate single-use consumption: captured token is used once; stored token
 * must become null so it cannot be reused on Retry.
 */
export function consumeTurnstileToken(
  currentToken: string | null,
): { captured: string | null; remaining: null } {
  return { captured: currentToken, remaining: null };
}
