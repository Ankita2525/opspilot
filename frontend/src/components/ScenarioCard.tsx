import { humanizeServiceName } from "@/lib/labels";
import type { Scenario } from "@/lib/types";

type ScenarioCardProps = {
  scenario: Scenario;
  selected: boolean;
  onSelect: () => void;
};

function ServiceIcon({ service }: { service: string }) {
  if (service === "auth-service") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden>
        <rect
          x="5"
          y="10"
          width="14"
          height="10"
          rx="2"
          stroke="currentColor"
          strokeWidth="1.6"
        />
        <path
          d="M8 10V7a4 4 0 0 1 8 0v3"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <circle cx="12" cy="15" r="1.4" fill="currentColor" />
      </svg>
    );
  }
  if (service === "payments-service") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden>
        <rect
          x="3"
          y="6"
          width="18"
          height="12"
          rx="2"
          stroke="currentColor"
          strokeWidth="1.6"
        />
        <path d="M3 10h18" stroke="currentColor" strokeWidth="1.6" />
        <path
          d="M7 15h4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden>
      <path
        d="M4 7h16v10H4z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M8 7V5h8v2M9 12h6M9 15h4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden>
      <path
        d="M3.5 8.2l2.8 2.8 6.2-6.4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ScenarioCard({
  scenario,
  selected,
  onSelect,
}: ScenarioCardProps) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className={selected ? "scenario-card selected" : "scenario-card"}
      onClick={onSelect}
    >
      <span className="scenario-card-icon" aria-hidden="true">
        <ServiceIcon service={scenario.affected_service} />
      </span>
      <span className="scenario-card-service">
        {humanizeServiceName(scenario.affected_service)}
      </span>
      <span className="scenario-card-title">{scenario.title}</span>
      <code className="scenario-card-id">{scenario.affected_service}</code>
      <span className="scenario-card-state">
        {selected ? (
          <>
            <CheckIcon />
            Selected
          </>
        ) : (
          "Select incident"
        )}
      </span>
    </button>
  );
}
