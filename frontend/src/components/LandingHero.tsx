import { ReasoningLoopViz } from "@/components/ReasoningLoopViz";

export function LandingHero() {
  return (
    <section className="landing-hero" aria-labelledby="product-heading">
      <div className="landing-hero-copy">
        <p className="type-kicker">OpsPilot</p>
        <h1 id="product-heading" className="type-display landing-hero-title">
          Autonomous Production
          <br />
          <span className="landing-hero-accent">Engineering Agent</span>
        </h1>
        <p className="landing-hero-lede">
          Start a live investigation against real sandbox services. OpsPilot
          gathers runtime evidence, forms a hypothesis, requests human approval
          for high-risk remediation, and verifies recovery with fresh telemetry.
        </p>
        <div className="capability-row">
          <article className="capability-card">
            <h2 className="capability-title">Safe by design</h2>
            <p className="capability-body">
              Ephemeral labs, isolated services, human-in-the-loop approvals.
            </p>
          </article>
          <article className="capability-card">
            <h2 className="capability-title">Evidence first</h2>
            <p className="capability-body">
              Live telemetry, runtime invariants, verifiable hypotheses.
            </p>
          </article>
        </div>
      </div>
      <div className="landing-hero-viz">
        <ReasoningLoopViz />
      </div>
    </section>
  );
}
