import { humanizeServiceName } from "@/lib/labels";
import type { Scenario } from "@/lib/types";

type ScenarioCardProps = {
  scenario: Scenario;
  selected: boolean;
  onSelect: () => void;
};

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
      <span className="scenario-card-service">
        {humanizeServiceName(scenario.affected_service)}
      </span>
      <span className="scenario-card-title">{scenario.title}</span>
      <code className="scenario-card-id">{scenario.affected_service}</code>
      <span className="scenario-card-state">
        {selected ? "Selected" : "Select incident"}
      </span>
    </button>
  );
}
