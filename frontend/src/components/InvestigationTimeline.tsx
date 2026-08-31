import { labelForStep } from "@/lib/labels";

type InvestigationTimelineProps = {
  steps: string[];
};

export function InvestigationTimeline({ steps }: InvestigationTimelineProps) {
  return (
    <section className="panel" aria-labelledby="investigation-heading">
      <h2 id="investigation-heading">Investigation</h2>
      <ol className="timeline">
        {steps.map((step, index) => (
          <li key={`${step}-${index}`} className="timeline-item">
            <span className="timeline-marker" aria-hidden="true">
              {index + 1}
            </span>
            <div>
              <p className="timeline-label">{labelForStep(step)}</p>
              <p className="timeline-state">Complete</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
