"""Shared cache-generation value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetVersion:
    """The ETL identity of one published logical dataset."""

    dataset_name: str
    version_no: int
    load_id: str
    source_watermark: str | None
    published_at: str
    status: str
    row_count: int | None = None
    checksum: str | None = None

    @property
    def token(self) -> tuple[str, int, str]:
        return self.dataset_name, self.version_no, self.load_id

    @property
    def generation_id(self) -> str:
        return "|".join((self.dataset_name, str(self.version_no), self.load_id))
