type Props = {
  affectedService: string;
  phase: "idle" | "traffic" | "degraded" | "rollback";
};

const NODES = [
  { id: "checkout-api", label: "checkout-api", x: 0, deps: ["PostgreSQL"] },
  { id: "auth-service", label: "auth-service", x: 1, deps: [] },
  { id: "payments-service", label: "payments-service", x: 2, deps: ["provider-service"] },
  { id: "provider-service", label: "provider-service", x: 3, deps: [] },
] as const;

export function ServiceTopology({ affectedService, phase }: Props) {
  return (
    <section className="panel topology-panel" aria-label="Service topology">
      <h2>Service topology</h2>
      <div className="topology-grid">
        {NODES.map((node) => {
          const active = node.id === affectedService;
          const degraded = active && phase === "degraded";
          const rolling = active && phase === "rollback";
          const flowing = phase === "traffic" || phase === "degraded";
          return (
            <div
              key={node.id}
              className={[
                "topology-node",
                active ? "topology-node-active" : "",
                degraded ? "topology-node-degraded" : "",
                rolling ? "topology-node-rollback" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <span className="topology-node-label">{node.label}</span>
              {node.deps.map((dep) => (
                <span
                  key={dep}
                  className={[
                    "topology-edge",
                    flowing && active ? "topology-edge-flow" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  → {dep}
                </span>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}
