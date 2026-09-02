type Step = {
  id: string;
  label: string;
  timestamp?: string | null;
  state: "pending" | "active" | "done";
};

type Props = {
  steps: Step[];
};

export function LifecycleTimeline({ steps }: Props) {
  return (
    <section className="panel lifecycle-panel" aria-label="Incident lifecycle">
      <h2>Incident lifecycle</h2>
      <ol className="lifecycle-track">
        {steps.map((step, index) => (
          <li
            key={step.id}
            className={[
              "lifecycle-step",
              `lifecycle-step-${step.state}`,
              index < steps.length - 1 ? "lifecycle-step-connected" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <span className="lifecycle-marker" aria-hidden />
            <div>
              <p className="lifecycle-label">{step.label}</p>
              {step.timestamp ? (
                <time className="lifecycle-time" dateTime={step.timestamp}>
                  {formatTime(step.timestamp)}
                </time>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      fractionalSecondDigits: 3,
    });
  } catch {
    return iso;
  }
}
