import { formatConfidence, humanizeIdentifier } from "@/lib/labels";
import type { LiveHypothesis } from "@/lib/live-incident";

type HypothesisPanelProps = {
  hypothesis: LiveHypothesis;
};

export function HypothesisPanel({ hypothesis }: HypothesisPanelProps) {
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
    </section>
  );
}
