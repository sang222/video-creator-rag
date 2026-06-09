from abc import ABC, abstractmethod


class MediaProvider(ABC):
    provider_name = "abstract"

    @abstractmethod
    def generate_asset(self, *, prompt: str, metadata: dict | None = None) -> dict:
        raise NotImplementedError

