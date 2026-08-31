import { formatConfidence, humanizeIdentifier } from "@/lib/labels";
import type { HypothesisResult } from "@/lib/types";

type HypothesisPanelProps = {
  result: HypothesisResult;
};

export function HypothesisPanel({ result }: HypothesisPanelProps) {
  const top = [...result.hypotheses].sort(
    (left, right) => right.confidence - left.confidence,
  )[0];

  if (!top) {
    return (
      <section className="panel" aria-labelledby="hypothesis-heading">
        <h2 id="hypothesis-heading">Root cause</h2>
        <p>No hypothesis was returned.</p>
      </section>
    );
  }

  return (
    <section className="panel" aria-labelledby="hypothesis-heading">
      <h2 id="hypothesis-heading">Root cause</h2>
      <p className="hypothesis-cause">{humanizeIdentifier(top.cause)}</p>
      <p className="hypothesis-confidence">
        Confidence {formatConfidence(top.confidence)}
      </p>
      <p className="hypothesis-summary">{result.reasoning_summary}</p>
      <h3 className="evidence-heading">Evidence</h3>
      <ul className="evidence-list">
        {top.evidence.map((item) => (
          <li key={`${item.source_type}-${item.summary}`}>
            <span className="evidence-source">
              {humanizeIdentifier(item.source_type)}
            </span>
            <span>{item.summary}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
