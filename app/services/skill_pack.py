from functools import lru_cache
from pathlib import Path

from app.config.settings import get_settings


REQUIRED_SKILL_PACKS = [
    "company_monetization_constitution.md",
    "default_workspace_playbook.md",
    "agents/authority_agent.md",
    "agents/script_agent.md",
    "agents/script_critic_agent.md",
    "agents/monetization_strategy_agent.md",
    "agents/seo_metadata_agent.md",
    "agents/publishing_content_agent.md",
    "agents/compliance_agent.md",
    "agents/memory_curator_agent.md",
]


class SkillPackLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().skill_dir

    def load(self, relative_path: str) -> str:
        path = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root not in path.parents and path != root:
            raise ValueError("skill path escapes configured skill directory")
        if not path.exists():
            raise FileNotFoundError(f"skill pack not found: {relative_path}")
        return path.read_text(encoding="utf-8").strip()

    def load_agent_prompt(self, agent_slug: str) -> str:
        return self.load(f"agents/{agent_slug}.md")

    def validate_required(self) -> dict[str, bool]:
        return {name: (self.root / name).exists() for name in REQUIRED_SKILL_PACKS}

    def assert_required(self) -> None:
        loaded = self.validate_required()
        missing = [name for name, exists in loaded.items() if not exists]
        if missing:
            raise FileNotFoundError(f"missing required skill pack(s): {', '.join(missing)}")


@lru_cache
def get_skill_loader() -> SkillPackLoader:
    return SkillPackLoader()
