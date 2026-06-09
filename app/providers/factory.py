from app.config.settings import Settings, get_settings
from app.providers.embedding import EmbeddingProvider, MockEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from app.providers.llm import LLMProvider, MockLLMProvider, OpenAICompatibleLLMProvider


class ProviderConfigurationError(RuntimeError):
    pass


def _with_fallback_reason(provider, reason: str):
    provider.fallback_reason = reason
    return provider


def _missing_llm_config_reason(settings: Settings) -> str:
    missing = [
        name
        for name, value in {
            "LLM_BASE_URL": settings.llm_base_url,
            "LLM_API_KEY": settings.llm_api_key,
            "LLM_MODEL": settings.llm_model,
        }.items()
        if not value
    ]
    return f"missing LLM provider config for real_text runtime: {', '.join(missing)}"


def _missing_embedding_config_reason(settings: Settings) -> str:
    missing = [
        name
        for name, value in {
            "EMBEDDING_BASE_URL/LLM_BASE_URL": settings.embedding_base_url,
            "EMBEDDING_API_KEY/LLM_API_KEY": settings.embedding_api_key,
            "EMBEDDING_MODEL": settings.embedding_model,
        }.items()
        if not value
    ]
    return f"missing embedding provider config for real_text runtime: {', '.join(missing)}"


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    runtime_mode = (settings.agent_runtime_mode or "mock").lower()
    provider = (settings.llm_provider or "mock").lower()
    has_http_config = bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)

    if runtime_mode == "mock":
        return MockLLMProvider()

    if provider in {"openai", "openai_compatible", "generic_http"}:
        if has_http_config:
            return OpenAICompatibleLLMProvider(
                base_url=settings.llm_base_url or "",
                api_key=settings.llm_api_key or "",
                model=settings.llm_model,
            )
        reason = _missing_llm_config_reason(settings)
        if settings.allow_provider_fallback_to_mock:
            return _with_fallback_reason(MockLLMProvider(), reason)
        raise ProviderConfigurationError(
            f"{reason}. Set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL, or enable "
            "ALLOW_PROVIDER_FALLBACK_TO_MOCK=true."
        )

    if runtime_mode == "real_text" and provider == "mock":
        reason = "LLM_PROVIDER=mock is not a real_text provider"
        if settings.allow_provider_fallback_to_mock:
            return _with_fallback_reason(MockLLMProvider(), reason)
        raise ProviderConfigurationError(f"{reason}. Use LLM_PROVIDER=openai_compatible or enable fallback.")

    if settings.use_mock_providers or provider == "mock" or not has_http_config:
        reason = "mock provider selected by USE_MOCK_PROVIDERS/provider config"
        return _with_fallback_reason(MockLLMProvider(), reason) if runtime_mode in {"hybrid", "real_text"} else MockLLMProvider()
    if provider in {"openai", "openai_compatible", "generic_http"}:
        return OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model,
        )
    return MockLLMProvider()


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    runtime_mode = (settings.agent_runtime_mode or "mock").lower()
    provider = (settings.embedding_provider or "mock").lower()
    has_http_config = bool(settings.embedding_base_url and settings.embedding_api_key and settings.embedding_model)
    if runtime_mode == "mock":
        return MockEmbeddingProvider(dimension=settings.embedding_dimension)

    if provider in {"openai", "openai_compatible", "generic_http"}:
        if has_http_config:
            return OpenAICompatibleEmbeddingProvider(
                base_url=settings.embedding_base_url or "",
                api_key=settings.embedding_api_key or "",
                model=settings.embedding_model,
            )
        reason = _missing_embedding_config_reason(settings)
        if settings.allow_provider_fallback_to_mock:
            return _with_fallback_reason(MockEmbeddingProvider(dimension=settings.embedding_dimension), reason)
        raise ProviderConfigurationError(
            f"{reason}. Set EMBEDDING_BASE_URL, EMBEDDING_API_KEY, and EMBEDDING_MODEL, or enable "
            "ALLOW_PROVIDER_FALLBACK_TO_MOCK=true."
        )

    if runtime_mode == "real_text" and provider == "mock":
        reason = "EMBEDDING_PROVIDER=mock is not a real_text provider"
        if settings.allow_provider_fallback_to_mock:
            return _with_fallback_reason(MockEmbeddingProvider(dimension=settings.embedding_dimension), reason)
        raise ProviderConfigurationError(f"{reason}. Use EMBEDDING_PROVIDER=openai_compatible or enable fallback.")

    if settings.use_mock_providers or provider == "mock" or not has_http_config:
        reason = "mock embedding selected by USE_MOCK_PROVIDERS/provider config"
        return _with_fallback_reason(MockEmbeddingProvider(dimension=settings.embedding_dimension), reason) if runtime_mode in {"hybrid", "real_text"} else MockEmbeddingProvider(dimension=settings.embedding_dimension)
    if provider in {"openai", "openai_compatible", "generic_http"}:
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url or "",
            api_key=settings.embedding_api_key or "",
            model=settings.embedding_model,
        )
    return MockEmbeddingProvider()
