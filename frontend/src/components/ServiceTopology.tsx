type Props = {
  affectedService: string;
  phase: "idle" | "traffic" | "degraded" | "rollback";
};

type GraphNode = {
  id: string;
  label: string;
  x: number;
  y: number;
  depOf?: string;
};

const GRAPH: GraphNode[] = [
  { id: "checkout-api", label: "checkout-api", x: 70, y: 48 },
  { id: "PostgreSQL", label: "PostgreSQL", x: 70, y: 150, depOf: "checkout-api" },
  { id: "auth-service", label: "auth-service", x: 230, y: 98 },
  { id: "payments-service", label: "payments-service", x: 390, y: 48 },
  {
    id: "provider-service",
    label: "provider-service",
    x: 390,
    y: 150,
    depOf: "payments-service",
  },
];

const EDGES: Array<{ from: string; to: string; path: string }> = [
  { from: "checkout-api", to: "PostgreSQL", path: "M 70 70 V 128" },
  { from: "payments-service", to: "provider-service", path: "M 390 70 V 128" },
];

function nodeClass(
  id: string,
  affectedService: string,
  phase: Props["phase"],
): string {
  const onPath =
    id === affectedService ||
    (affectedService === "checkout-api" && id === "PostgreSQL") ||
    (affectedService === "payments-service" && id === "provider-service");

  if (!onPath) {
    return "topo-node topo-node-idle";
  }
  if (phase === "degraded") {
    return "topo-node topo-node-degraded";
  }
  if (phase === "rollback") {
    return "topo-node topo-node-recovered";
  }
  if (phase === "traffic") {
    return "topo-node topo-node-investigating";
  }
  return "topo-node topo-node-active";
}

function edgeClass(
  from: string,
  affectedService: string,
  phase: Props["phase"],
): string {
  const active =
    (affectedService === "checkout-api" && from === "checkout-api") ||
    (affectedService === "payments-service" && from === "payments-service");
  if (!active) {
    return "topo-edge";
  }
  if (phase === "degraded") {
    return "topo-edge topo-edge-degraded";
  }
  if (phase === "rollback") {
    return "topo-edge topo-edge-recovered";
  }
  return "topo-edge topo-edge-flow";
}

export function ServiceTopology({ affectedService, phase }: Props) {
  return (
    <section className="panel topology-panel" aria-label="Service topology">
      <h2>Service topology</h2>
      <svg
        className="topology-svg"
        viewBox="0 0 460 210"
        role="img"
        aria-label={`Topology highlighting ${affectedService}`}
      >
        <defs>
          <marker
            id="topo-arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="topo-arrow-head" />
          </marker>
        </defs>
        {EDGES.map((edge) => (
          <path
            key={`${edge.from}-${edge.to}`}
            d={edge.path}
            className={edgeClass(edge.from, affectedService, phase)}
            markerEnd="url(#topo-arrow)"
            fill="none"
          />
        ))}
        {GRAPH.map((node) => (
          <g
            key={node.id}
            className={nodeClass(node.id, affectedService, phase)}
            transform={`translate(${node.x}, ${node.y})`}
          >
            <rect x="-58" y="-22" width="116" height="44" rx="10" />
            <text textAnchor="middle" y="5" className="topo-node-text">
              {node.label}
            </text>
          </g>
        ))}
      </svg>
      <ul className="topology-mobile-list">
        {GRAPH.filter((n) => !n.depOf).map((node) => {
          const dep = GRAPH.find((d) => d.depOf === node.id);
          const active = node.id === affectedService;
          return (
            <li
              key={node.id}
              className={[
                "topology-mobile-item",
                active ? "topology-mobile-active" : "",
                active && phase === "degraded" ? "topology-mobile-degraded" : "",
                active && phase === "rollback" ? "topology-mobile-recovered" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <code>{node.label}</code>
              {dep ? (
                <span className="topology-mobile-dep">→ {dep.label}</span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
