# OpsPilot Engineering Rules

OpsPilot is an autonomous production engineering agent.

## Priorities

1. Reliability first.
2. Keep implementation simple.
3. Prefer proven technologies.
4. Every feature must support the core incident workflow.
5. Avoid unnecessary infrastructure or dependencies.

## Architecture Rules

- Python 3.12 only.
- Use FastAPI for the backend.
- Use Pydantic for typed schemas.
- Use LangGraph for agent orchestration.
- Use MCP for tool exposure.
- PostgreSQL will be the primary persistent store.
- Use OpenTelemetry for observability.
- Keep model providers abstracted.
- Prefer deterministic Python logic over LLM reasoning when possible.
- High-risk actions must require human approval.
- Never expose chain-of-thought; expose concise reasoning summaries and evidence only.

## Do Not Add Without Explicit Approval

- Redis
- Kafka
- Kubernetes
- dedicated vector databases
- additional agent frameworks
- paid APIs/services
- experimental infrastructure
- unnecessary abstractions

## Development Rules

- Make small changes.
- Add tests for new behavior.
- Run tests after every meaningful change.
- Do not change architecture silently.
- Do not add dependencies unless required.
- Do not fabricate metrics or results.
- Preserve the $0 project-cost requirement.
