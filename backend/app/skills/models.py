from pydantic import BaseModel, ConfigDict


class Skill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    diagnostic_steps: list[str]
    safety_rules: list[str]
    verification_steps: list[str]
    source_path: str
