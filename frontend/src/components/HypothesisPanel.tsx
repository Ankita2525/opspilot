import { formatConfidence, humanizeIdentifier } from "@/lib/labels";
import {
  NO_SUPPORTED_ACTION,
  type LiveHypothesis,
} from "@/lib/live-incident";

type HypothesisPanelProps = {
  hypothesis: LiveHypothesis;
};

export function HypothesisPanel({ hypothesis }: HypothesisPanelProps) {
  const unsupported = hypothesis.recommendedAction === NO_SUPPORTED_ACTION;
  return (
    <section className="panel" aria-labelledby="hypothesis-heading">
      <h2 id="hypothesis-heading">Root cause</h2>
      <p className="hypothesis-cause">
        {humanizeIdentifier(hypothesis.rootCause)}
      </p>
      <p className="hypothesis-confidence">
        Confidence {formatConfidence(hypothesis.confidence)}
      </p>
      <p className="hypothesis-summary">
        Recommended action: {humanizeIdentifier(hypothesis.recommendedAction)}
      </p>
      {hypothesis.recommendationSummary ? (
        <p className="hypothesis-recommendation">
          {hypothesis.recommendationSummary}
        </p>
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
