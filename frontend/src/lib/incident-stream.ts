import { incidentStartPayload } from "./api";
import {
  consumeSseBuffer,
  IncidentStreamError,
  isAbortError,
  parseInvestigationEvent,
  STREAM_FAILURE_MESSAGE,
} from "./sse-parser";
import { preIncidentErrorFromResponse } from "./start-rejection";
import type { InvestigationEvent } from "./types";

const TERMINAL_EVENT_TYPES = new Set([
  "approval_required",
  "incident_completed",
  "incident_failed",
  "investigation_blocked",
]);

export async function streamIncident(options: {
  scenarioId: string;
  turnstileToken?: string | null;
  onEvent: (event: InvestigationEvent) => void;
  signal?: AbortSignal;
}): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/incidents/stream", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(
        incidentStartPayload(options.scenarioId, options.turnstileToken),
      ),
      signal: options.signal,
    });
  } catch (cause) {
    if (isAbortError(cause)) {
      throw cause;
    }
    throw new IncidentStreamError();
  }

  if (!response.ok) {
    // Pre-incident gate: retain status + backend error code for truthful UX.
    throw await preIncidentErrorFromResponse(response);
  }
  if (!response.body) {
    throw new IncidentStreamError();
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminal = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const consumed = consumeSseBuffer(buffer);
      buffer = consumed.rest;
      for (const frame of consumed.frames) {
        const event = parseInvestigationEvent(frame);
        options.onEvent(event);
        if (TERMINAL_EVENT_TYPES.has(event.event_type)) {
          sawTerminal = true;
        }
      }
    }
  } catch (cause) {
    if (isAbortError(cause) || cause instanceof IncidentStreamError) {
      throw cause;
    }
    throw new IncidentStreamError();
  } finally {
    reader.releaseLock();
  }

  if (options.signal?.aborted) {
    return;
  }

  if (buffer.trim().length > 0 && !sawTerminal) {
    const event = parseInvestigationEvent(buffer);
    options.onEvent(event);
    if (TERMINAL_EVENT_TYPES.has(event.event_type)) {
      sawTerminal = true;
    }
  }

  if (!sawTerminal) {
    throw new IncidentStreamError(STREAM_FAILURE_MESSAGE);
  }
}
