from abc import ABC, abstractmethod


class AnalyticsProvider(ABC):
    provider_name = "abstract"

    @abstractmethod
    def fetch_snapshot(self, *, platform_video_id: str) -> dict:
        raise NotImplementedError

