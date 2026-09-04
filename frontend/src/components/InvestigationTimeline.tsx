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
    <section
      className="panel investigation-panel"
      aria-labelledby="investigation-heading"
    >
      <h2 id="investigation-heading">Investigation</h2>
      <ol className="investigation-rail">
        {steps.map((step, index) => (
          <li
            key={step.id}
            className={`investigation-step investigation-step-${step.status}`}
          >
            <span className="investigation-marker" aria-hidden="true">
              {step.status === "completed" ? (
                <svg viewBox="0 0 16 16" width="14" height="14" fill="none">
                  <path
                    d="M3.5 8.2l2.8 2.8 6.2-6.4"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              ) : step.status === "running" ? (
                <span className="timeline-spinner" />
              ) : (
                index + 1
              )}
            </span>
            <div className="investigation-copy">
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
