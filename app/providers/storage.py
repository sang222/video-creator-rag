from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageProvider(ABC):
    provider_name = "abstract"

    @abstractmethod
    def put_object(self, *, key: str, body: bytes | BinaryIO, content_type: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_uri(self, *, key: str) -> str:
        raise NotImplementedError

