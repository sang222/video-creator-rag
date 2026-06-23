from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfigCatalogVersionCreate(BaseModel):
    catalog_key: str
    catalog_version: str
    schema_version: str
    content: dict[str, Any]
    content_hash: str
    source_path: str
    status: Literal["draft", "active", "retired"] = "active"

    model_config = ConfigDict(extra="forbid")


class CatalogDocument(BaseModel):
    catalog_key: str
    catalog_version: str
    schema_version: str
    status: Literal["draft", "active", "retired"] = "active"
    items: list[dict[str, Any]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")
