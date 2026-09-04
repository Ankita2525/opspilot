import type { LifecycleStep } from "@/lib/command-center";

type Props = {
  steps: LifecycleStep[];
};

export function LifecycleRail({ steps }: Props) {
  return (
    <section className="panel lifecycle-rail-panel" aria-label="Incident lifecycle">
      <h2>Incident lifecycle</h2>
      <ol className="lifecycle-rail">
        {steps.map((step, index) => (
          <li
            key={step.id}
            className={[
              "lifecycle-rail-step",
              `lifecycle-rail-${step.state}`,
              `lifecycle-rail-tone-${step.tone}`,
              index < steps.length - 1 ? "lifecycle-rail-connected" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <div className="lifecycle-rail-trackline">
              <span className="lifecycle-rail-dot" aria-hidden />
              {index < steps.length - 1 ? (
                <span className="lifecycle-rail-connector" aria-hidden />
              ) : null}
            </div>
            <p className="lifecycle-rail-label">{step.label}</p>
            <span className="sr-only">{step.state}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
