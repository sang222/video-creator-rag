"""Compatibility guard for removed runtime test-double providers."""


class RuntimeMockProviderRemoved(RuntimeError):
    pass


def __getattr__(name: str):
    raise RuntimeMockProviderRemoved(
        "Runtime mock providers were removed from production. Use tests/fakes only."
    )
