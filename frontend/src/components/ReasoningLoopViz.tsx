const LOOP_STEPS = [
  {
    id: "evidence",
    label: "Evidence",
    detail: "Collect runtime signals",
    boxX: 64,
    boxY: 32,
    accent: false,
  },
  {
    id: "hypothesis",
    label: "Hypothesis",
    detail: "Correlate & explain",
    boxX: 292,
    boxY: 32,
    accent: false,
  },
  {
    id: "action",
    label: "Action",
    detail: "Propose remediation",
    boxX: 292,
    boxY: 184,
    accent: true,
  },
  {
    id: "verification",
    label: "Verification",
    detail: "Validate recovery",
    boxX: 64,
    boxY: 184,
    accent: false,
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
        viewBox="0 0 420 300"
        role="img"
        aria-hidden="true"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <pattern
            id="reasoning-grid"
            width="18"
            height="18"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 18 0 L 0 0 0 18"
              fill="none"
              stroke="currentColor"
              strokeWidth="0.6"
              className="reasoning-grid-stroke"
            />
          </pattern>
          <filter id="node-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <filter id="hub-glow" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="3.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <rect
          x="10"
          y="10"
          width="400"
          height="280"
          rx="14"
          className="reasoning-frame"
          fill="url(#reasoning-grid)"
        />

        {/* Faint infrastructure planes */}
        <g className="reasoning-planes" opacity="0.22">
          <rect x="150" y="108" width="120" height="72" rx="8" />
          <rect x="168" y="98" width="84" height="18" rx="4" />
          <rect x="176" y="188" width="68" height="14" rx="3" />
        </g>

        <g className="reasoning-cubes" opacity="0.4">
          <rect x="186" y="126" width="22" height="22" rx="3" />
          <rect x="214" y="134" width="18" height="18" rx="3" />
          <rect x="198" y="152" width="14" height="14" rx="2" />
        </g>

        {/* Loop connectors */}
        <g className="reasoning-paths">
          <path
            className="reasoning-path"
            d="M 106 74 H 314"
            fill="none"
            strokeWidth="1.4"
          />
          <path
            className="reasoning-path reasoning-path-amber"
            d="M 334 94 V 204"
            fill="none"
            strokeWidth="1.4"
          />
          <path
            className="reasoning-path"
            d="M 314 226 H 106"
            fill="none"
            strokeWidth="1.4"
          />
          <path
            className="reasoning-path"
            d="M 86 204 V 94"
            fill="none"
            strokeWidth="1.4"
          />
          <path
            className="reasoning-path reasoning-path-hub"
            d="M 210 148 L 106 74 M 210 148 L 314 74 M 210 148 L 314 226 M 210 148 L 106 226"
            fill="none"
            strokeWidth="1"
          />
        </g>

        {/* Central OpsPilot node */}
        <g className="reasoning-hub" filter="url(#hub-glow)">
          <circle cx="210" cy="148" r="36" className="reasoning-hub-outer" />
          <circle cx="210" cy="148" r="28" className="reasoning-hub-ring" />
          <circle cx="210" cy="148" r="18" className="reasoning-hub-core" />
          <circle cx="210" cy="148" r="5" className="reasoning-hub-pulse" />
          <text
            x="210"
            y="152"
            textAnchor="middle"
            className="reasoning-hub-label"
          >
            OP
          </text>
        </g>

        {LOOP_STEPS.map((step) => {
          const cx = step.boxX + 21;
          const cy = step.boxY + 21;
          return (
            <g
              key={step.id}
              className={
                step.accent
                  ? "reasoning-node reasoning-node-action"
                  : "reasoning-node"
              }
            >
              <rect
                x={step.boxX}
                y={step.boxY}
                width="42"
                height="42"
                rx="9"
                className="reasoning-node-box"
                filter="url(#node-glow)"
              />
              <circle
                cx={cx}
                cy={cy}
                r="4.5"
                className="reasoning-node-dot"
              />
              <circle
                cx={cx}
                cy={cy}
                r="7"
                className="reasoning-endpoint-pulse"
              />
              <text
                x={cx}
                y={step.boxY + 58}
                textAnchor="middle"
                className="reasoning-node-title"
              >
                {step.label.toUpperCase()}
              </text>
              <text
                x={cx}
                y={step.boxY + 72}
                textAnchor="middle"
                className="reasoning-node-detail"
              >
                {step.detail}
              </text>
            </g>
          );
        })}
      </svg>

      <ol className="reasoning-loop-legend">
        {[
          LOOP_STEPS[0],
          LOOP_STEPS[1],
          LOOP_STEPS[3],
          LOOP_STEPS[2],
        ].map((step) => (
          <li
            key={step.id}
            className={step.accent ? "reasoning-legend-action" : undefined}
          >
            <span className="reasoning-legend-label">{step.label}</span>
            <span className="reasoning-legend-detail">{step.detail}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
