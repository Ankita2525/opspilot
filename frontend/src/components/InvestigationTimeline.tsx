import type { TimelineStep } from "@/lib/live-incident";

type InvestigationTimelineProps = {
  steps: TimelineStep[];
  skills?: string[];
  symptomSummary?: string | null;
};

export function InvestigationTimeline({
  steps,
  skills = [],
  symptomSummary,
}: InvestigationTimelineProps) {
  return (
    <section className="panel" aria-labelledby="investigation-heading">
      <h2 id="investigation-heading">Investigation</h2>
      <ol className="timeline">
        {steps.map((step, index) => (
          <li
            key={step.id}
            className={`timeline-item timeline-item-${step.status}`}
          >
            <span className="timeline-marker" aria-hidden="true">
              {step.status === "running" ? (
                <span className="timeline-spinner" />
              ) : (
                index + 1
              )}
            </span>
            <div>
              <p className="timeline-label">{step.label}</p>
              <p className="timeline-state">{statusLabel(step.status)}</p>
            </div>
          </li>
        ))}
      </ol>
      {symptomSummary ? (
        <p className="context-brief">{symptomSummary}</p>
      ) : null}
      {skills.length > 0 ? (
        <div className="skills-block">
          <p className="skills-heading">Loaded diagnostic skills</p>
          <ul className="skills-list">
            {skills.map((skill) => (
              <li key={skill} className="skill-chip">
                {skill}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function statusLabel(status: TimelineStep["status"]): string {
  if (status === "completed") {
    return "Complete";
  }
  if (status === "running") {
    return "In progress";
  }
  return "Pending";
}
