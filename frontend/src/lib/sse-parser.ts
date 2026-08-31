import {
  INVESTIGATION_EVENT_TYPES,
  type InvestigationEvent,
  type InvestigationEventType,
} from "./types.ts";

export const STREAM_FAILURE_MESSAGE = "Investigation could not be completed.";

export class IncidentStreamError extends Error {
  constructor(message = STREAM_FAILURE_MESSAGE) {
    super(message);
    this.name = "IncidentStreamError";
  }
}

const FRAME_DELIMITER = /\r?\n\r?\n/;

export function consumeSseBuffer(buffer: string): {
  frames: string[];
  rest: string;
} {
  const frames: string[] = [];
  let rest = buffer;

  while (true) {
    const match = FRAME_DELIMITER.exec(rest);
    if (match === null || match.index === undefined) {
      break;
    }
    const raw = rest.slice(0, match.index);
    rest = rest.slice(match.index + match[0].length);
    if (raw.trim().length > 0) {
      frames.push(raw);
    }
  }

  return { frames, rest };
}

export function parseSseFields(raw: string): {
  event: string | null;
  data: string;
} {
  let event: string | null = null;
  const dataLines: string[] = [];

  for (const line of raw.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      event = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  return { event, data: dataLines.join("\n") };
}

export function parseInvestigationEvent(rawFrame: string): InvestigationEvent {
  const fields = parseSseFields(rawFrame);
  if (!fields.data) {
    throw new IncidentStreamError();
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(fields.data);
  } catch {
    throw new IncidentStreamError();
  }

  const event = asInvestigationEvent(parsed);
  if (
    fields.event !== null &&
    fields.event !== event.event_type
  ) {
    throw new IncidentStreamError();
  }
  return event;
}

function asInvestigationEvent(value: unknown): InvestigationEvent {
  if (!isRecord(value)) {
    throw new IncidentStreamError();
  }

  const eventType = value.event_type;
  if (!isInvestigationEventType(eventType)) {
    throw new IncidentStreamError();
  }

  const incidentId = value.incident_id;
  const sequence = value.sequence;
  const timestamp = value.timestamp;
  const message = value.message;
  const step = value.step;
  const data = value.data;

  if (typeof incidentId !== "string" || incidentId.length === 0) {
    throw new IncidentStreamError();
  }
  if (typeof sequence !== "number" || !Number.isInteger(sequence) || sequence < 1) {
    throw new IncidentStreamError();
  }
  if (typeof timestamp !== "string" || timestamp.length === 0) {
    throw new IncidentStreamError();
  }
  if (typeof message !== "string") {
    throw new IncidentStreamError();
  }
  if (step !== null && step !== undefined && typeof step !== "string") {
    throw new IncidentStreamError();
  }
  if (!isRecord(data)) {
    throw new IncidentStreamError();
  }

  return {
    event_type: eventType,
    incident_id: incidentId,
    sequence,
    timestamp,
    step: typeof step === "string" ? step : null,
    message,
    data,
  };
}

export function isInvestigationEventType(
  value: unknown,
): value is InvestigationEventType {
  return (
    typeof value === "string" &&
    (INVESTIGATION_EVENT_TYPES as readonly string[]).includes(value)
  );
}

export function isAbortError(cause: unknown): boolean {
  return (
    (cause instanceof DOMException && cause.name === "AbortError") ||
    (cause instanceof Error && cause.name === "AbortError")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
