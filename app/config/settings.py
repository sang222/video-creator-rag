from functools import lru_cache
from pathlib import Path
import os


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.app_name = "Company-level Editorial Content Operating System"
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./phase1.db")
        self.policy_dir = Path(os.getenv("POLICY_DIR", "app/policies"))
        self.storage_root = Path(os.getenv("STORAGE_ROOT", "local_storage"))
        self.skill_dir = Path(os.getenv("SKILL_DIR", "app/skills"))
        self.agent_runtime_mode = os.getenv("AGENT_RUNTIME_MODE", "mock").lower()
        self.use_mock_providers = _env_bool("USE_MOCK_PROVIDERS", "true")
        self.allow_provider_fallback_to_mock = _env_bool("ALLOW_PROVIDER_FALLBACK_TO_MOCK", "true")
        self.validate_skill_packs_on_startup = _env_bool("VALIDATE_SKILL_PACKS_ON_STARTUP", "false")
        self.llm_provider = os.getenv("LLM_PROVIDER", "mock")
        self.llm_base_url = os.getenv("LLM_BASE_URL")
        self.llm_api_key = os.getenv("LLM_API_KEY")
        self.llm_model = os.getenv("LLM_MODEL", "mock-llm")
        self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "mock")
        self.embedding_base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("LLM_BASE_URL")
        self.embedding_api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY")
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mock-hash-embedding")
        self.embedding_dimension = int(os.getenv("EMBEDDING_DIMENSION", "16"))
        self.memory_retrieval_backend = os.getenv("MEMORY_RETRIEVAL_BACKEND", "auto").lower()
        self.enable_pgvector = _env_bool("ENABLE_PGVECTOR", "true")
        self.memory_vector_top_k = int(os.getenv("MEMORY_VECTOR_TOP_K", "5"))
        self.memory_keyword_fallback_enabled = _env_bool("MEMORY_KEYWORD_FALLBACK_ENABLED", "true")
        self.max_agent_context_tokens = int(os.getenv("MAX_AGENT_CONTEXT_TOKENS", "6000"))
        self.max_skill_context_tokens = int(os.getenv("MAX_SKILL_CONTEXT_TOKENS", "1200"))
        self.max_memory_context_tokens = int(os.getenv("MAX_MEMORY_CONTEXT_TOKENS", "2500"))
        self.max_constitution_tokens = int(os.getenv("MAX_CONSTITUTION_TOKENS", "1000"))
        self.max_task_input_tokens = int(os.getenv("MAX_TASK_INPUT_TOKENS", "1500"))
        self.max_repair_attempts = int(os.getenv("MAX_REPAIR_ATTEMPTS", "1"))
        self.agent_call_timeout_seconds = int(os.getenv("AGENT_CALL_TIMEOUT_SECONDS", "120"))

        if self.embedding_dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if self.memory_retrieval_backend not in {"auto", "pgvector", "fallback"}:
            raise ValueError("MEMORY_RETRIEVAL_BACKEND must be auto, pgvector, or fallback")
        if self.memory_vector_top_k <= 0:
            raise ValueError("MEMORY_VECTOR_TOP_K must be positive")


@lru_cache
def get_settings() -> Settings:
    return Settings()
