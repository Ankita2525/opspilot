import { formatConfidence, humanizeIdentifier } from "@/lib/labels";
import {
  NO_SUPPORTED_ACTION,
  type LiveHypothesis,
} from "@/lib/live-incident";

type HypothesisPanelProps = {
  hypothesis: LiveHypothesis;
  skills?: string[];
  evidenceCount?: number;
};

export function HypothesisPanel({
  hypothesis,
  skills = [],
  evidenceCount,
}: HypothesisPanelProps) {
  const unsupported = hypothesis.recommendedAction === NO_SUPPORTED_ACTION;
  const rootCauseDisplay = formatRootCause(hypothesis.rootCause);
  return (
    <section
      className="panel root-cause-panel"
      aria-labelledby="hypothesis-heading"
    >
      <p className="type-kicker">Root cause</p>
      <h2 id="hypothesis-heading" className="hypothesis-cause">
        {rootCauseDisplay}
      </h2>
      <p className="hypothesis-confidence type-mono">
        Confidence {formatConfidence(hypothesis.confidence)}
      </p>
      <p className="hypothesis-badge">Evidence-based hypothesis</p>
      {typeof evidenceCount === "number" ? (
        <p className="hypothesis-evidence-count type-mono">
          {evidenceCount} bounded evidence item{evidenceCount === 1 ? "" : "s"}
        </p>
      ) : null}
      <div className="hypothesis-action-block">
        <p className="skills-heading">Recommended action</p>
        <p className="hypothesis-summary">
          {humanizeIdentifier(hypothesis.recommendedAction)}
        </p>
        {hypothesis.recommendationSummary ? (
          <p className="hypothesis-recommendation">
            {hypothesis.recommendationSummary}
          </p>
        ) : null}
      </div>
      {skills.length > 0 ? (
        <div className="skills-block">
          <p className="skills-heading">Diagnostic skills</p>
          <ul className="skills-list">
            {skills.map((skill) => (
              <li key={skill} className="skill-chip">
                {skill}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {unsupported ? (
        <p className="hypothesis-outcome">
          Investigation complete. No supported automated remediation was
          selected. Production remains unchanged.
        </p>
      ) : null}
    </section>
  );
}

function formatRootCause(value: string): string {
  if (/\s/.test(value) || (!value.includes("_") && !value.includes("-"))) {
    return value;
  }
  return humanizeIdentifier(value);
}
