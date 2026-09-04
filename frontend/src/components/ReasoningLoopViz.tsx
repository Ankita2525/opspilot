const LOOP_STEPS = [
  {
    id: "evidence",
    label: "Evidence",
    detail: "Collect runtime signals",
    x: 48,
    y: 36,
  },
  {
    id: "hypothesis",
    label: "Hypothesis",
    detail: "Correlate & explain",
    x: 312,
    y: 36,
  },
  {
    id: "action",
    label: "Action",
    detail: "Propose remediation",
    x: 312,
    y: 204,
    accent: true,
  },
  {
    id: "verification",
    label: "Verification",
    detail: "Validate recovery",
    x: 48,
    y: 204,
  },
] as const;

export function ReasoningLoopViz() {
  return (
    <div className="reasoning-loop" aria-labelledby="reasoning-loop-title">
      <h2 id="reasoning-loop-title" className="sr-only">
        OpsPilot reasoning loop: evidence, hypothesis, action, verification
      </h2>
      <svg
        className="reasoning-loop-svg"
        viewBox="0 0 400 280"
        role="img"
        aria-hidden="true"
      >
        <defs>
          <pattern
            id="reasoning-grid"
            width="16"
            height="16"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 16 0 L 0 0 0 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="0.5"
              className="reasoning-grid-stroke"
            />
          </pattern>
          <filter id="node-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <rect
          x="8"
          y="8"
          width="384"
          height="264"
          rx="12"
          className="reasoning-frame"
          fill="url(#reasoning-grid)"
        />

        {/* Infrastructure cubes */}
        <g className="reasoning-cubes" opacity="0.35">
          <rect x="168" y="108" width="28" height="28" rx="3" />
          <rect x="204" y="118" width="22" height="22" rx="3" />
          <rect x="186" y="142" width="18" height="18" rx="2" />
        </g>

        {/* Connection paths */}
        <g className="reasoning-paths">
          <path
            className="reasoning-path"
            d="M 108 68 H 252"
            fill="none"
            strokeWidth="1.5"
          />
          <path
            className="reasoning-path reasoning-path-amber"
            d="M 332 100 V 168"
            fill="none"
            strokeWidth="1.5"
          />
          <path
            className="reasoning-path"
            d="M 252 236 H 108"
            fill="none"
            strokeWidth="1.5"
          />
          <path
            className="reasoning-path"
            d="M 68 168 V 100"
            fill="none"
            strokeWidth="1.5"
          />
          <path
            className="reasoning-path reasoning-path-hub"
            d="M 200 140 L 108 68 M 200 140 L 292 68 M 200 140 L 292 204 M 200 140 L 108 204"
            fill="none"
            strokeWidth="1"
            opacity="0.45"
          />
        </g>

        {/* Hub */}
        <g className="reasoning-hub" filter="url(#node-glow)">
          <circle cx="200" cy="140" r="28" className="reasoning-hub-ring" />
          <circle cx="200" cy="140" r="18" className="reasoning-hub-core" />
          <circle cx="200" cy="140" r="6" className="reasoning-hub-pulse" />
          <text
            x="200"
            y="144"
            textAnchor="middle"
            className="reasoning-hub-label"
          >
            OP
          </text>
        </g>

        {LOOP_STEPS.map((step) => (
          <g
            key={step.id}
            className={
              "accent" in step && step.accent
                ? "reasoning-node reasoning-node-action"
                : "reasoning-node"
            }
            transform={`translate(${step.x}, ${step.y})`}
          >
            <rect
              width="40"
              height="40"
              rx="8"
              className="reasoning-node-box"
            />
            <circle cx="20" cy="20" r="4" className="reasoning-node-dot" />
            <text x="48" y="16" className="reasoning-node-title">
              {step.label.toUpperCase()}
            </text>
            <text x="48" y="32" className="reasoning-node-detail">
              {step.detail}
            </text>
          </g>
        ))}
      </svg>

      <ol className="reasoning-loop-legend">
        {LOOP_STEPS.map((step) => (
          <li key={step.id}>
            <span className="reasoning-legend-label">{step.label}</span>
            <span className="reasoning-legend-detail">{step.detail}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
