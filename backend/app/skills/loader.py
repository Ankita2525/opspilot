from pathlib import Path

from backend.app.skills.models import Skill

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SKILLS_DIR = _REPO_ROOT / "skills"
_REQUIRED_SECTIONS = (
    "description",
    "diagnostic steps",
    "safety rules",
    "verification steps",
)


class SkillLoader:
    """Load procedural SKILL.md files from the repository skills directory."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        self._cache: dict[str, Skill] = {}

    def list_skills(self) -> list[str]:
        if not self._skills_dir.is_dir():
            return []
        names = [
            path.name
            for path in self._skills_dir.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        ]
        return sorted(names)

    def load(self, skill_name: str) -> Skill:
        cached = self._cache.get(skill_name)
        if cached is not None:
            return cached
        path = self._resolve_skill_path(skill_name)
        skill = parse_skill_markdown(path.read_text(encoding="utf-8"), source_path=str(path))
        self._cache[skill_name] = skill
        return skill

    def _resolve_skill_path(self, skill_name: str) -> Path:
        skills_root = self._skills_dir.resolve()
        candidate = (self._skills_dir / skill_name / "SKILL.md").resolve()
        if not candidate.is_relative_to(skills_root) or not candidate.is_file():
            raise ValueError(f"Unknown skill: {skill_name}")
        return candidate


def parse_skill_markdown(markdown: str, *, source_path: str) -> Skill:
    title = None
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            if title is None and current is None:
                title = line[2:].strip()
            continue
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)

    if not title:
        raise ValueError(f"Skill is missing a title: {source_path}")
    for heading in _REQUIRED_SECTIONS:
        if heading not in sections:
            raise ValueError(f"Skill is missing section '{heading}': {source_path}")

    description = _paragraph(sections["description"])
    diagnostic_steps = _bullets(sections["diagnostic steps"])
    safety_rules = _bullets(sections["safety rules"])
    verification_steps = _bullets(sections["verification steps"])
    if not description:
        raise ValueError(f"Skill is missing a description: {source_path}")
    if not diagnostic_steps:
        raise ValueError(f"Skill is missing diagnostic steps: {source_path}")
    if not safety_rules:
        raise ValueError(f"Skill is missing safety rules: {source_path}")
    if not verification_steps:
        raise ValueError(f"Skill is missing verification steps: {source_path}")

    return Skill(
        name=title,
        description=description,
        diagnostic_steps=diagnostic_steps,
        safety_rules=safety_rules,
        verification_steps=verification_steps,
        source_path=source_path,
    )


def _paragraph(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip())


def _bullets(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items
