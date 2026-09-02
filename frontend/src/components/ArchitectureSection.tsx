export function ArchitectureSection() {
  return (
    <section className="panel architecture-panel" aria-labelledby="arch-heading">
      <h2 id="arch-heading">Architecture</h2>
      <div className="architecture-columns">
        <article>
          <h3>Full production architecture</h3>
          <p>
            Docker Compose on a single VM with Caddy TLS, segmented private networks,
            co-located Prometheus, OTEL collector, durable PostgreSQL safety state, and
            sandbox services isolated from public ingress.
          </p>
        </article>
        <article>
          <h3>Public ephemeral live incident lab</h3>
          <p>
            Cloud Run multi-container runtime with the same service and telemetry
            boundaries, real controlled faults, and live-mode invariants — using an
            ephemeral runtime that scales to zero when the demo is idle to avoid
            continuously allocated infrastructure.
          </p>
        </article>
      </div>
    </section>
  );
}
