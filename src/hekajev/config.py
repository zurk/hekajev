import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Limits(StrictModel):
    input_bytes: int = Field(default=32_768, ge=2_048, le=1_000_000)
    max_chunks: int = Field(default=20, ge=1, le=1000)


class Input(StrictModel):
    repository: str = Field(min_length=1)
    revision: str = Field(default="HEAD", min_length=1)
    path_patterns: list[str] = Field(default_factory=list)

    @property
    def is_remote(self) -> bool:
        return "://" in self.repository or bool(re.match(r"(?:[\w.-]+@)?[\w.-]+:", self.repository))

    @model_validator(mode="after")
    def valid_selection(self):
        from hekajev.git import Repository
        from hekajev.sources import _remote

        if self.revision.startswith("-"):
            raise ValueError("Revision cannot start with '-'")
        Repository.pathspecs(self.path_patterns)
        if self.is_remote:
            _remote(self.repository)
        return self


class Filter(StrictModel):
    question: str = Field(min_length=1)
    aggregation: Literal["any", "all"] = "any"


class Classification(StrictModel):
    mode: Literal["single", "multi"] = "single"
    question: str = Field(min_length=1)
    categories: dict[str, str] = Field(min_length=1, max_length=255)
    threshold: float | None = Field(default=None, gt=0, le=1)
    aggregation: Literal["consensus", "any", "all"] | None = None

    @model_validator(mode="after")
    def has_uncertainty(self):
        if not self.question.strip():
            raise ValueError("Classification question must not be empty")
        if self.mode == "single" and "uncertain" not in self.categories:
            raise ValueError("Each classification needs an 'uncertain' category")
        if self.mode == "multi" and "uncertain" in self.categories:
            raise ValueError("Multi uncertainty is separate from categories")
        if self.mode == "single" and len(self.categories) < 2:
            raise ValueError("Single classification needs at least two categories")
        if self.mode == "single" and self.aggregation not in (None, "consensus"):
            raise ValueError("Single classification uses consensus aggregation")
        if self.mode == "multi" and self.aggregation == "consensus":
            raise ValueError("Multi classification uses any or all aggregation")
        if self.mode == "multi" and self.threshold is not None and self.threshold <= 0.5:
            raise ValueError("Multi classification threshold must be greater than 0.5")
        if any(not key.strip() or not value.strip() for key, value in self.categories.items()):
            raise ValueError("Category names and descriptions must not be empty")
        return self


class Config(StrictModel):
    inputs: list[Input] = Field(default_factory=list)
    filters: list[str | Filter] = Field(min_length=1)
    filter_mode: Literal["and", "or"] = "and"
    classifications: dict[str, Classification] = Field(min_length=1)
    instructions: str = ""
    limits: Limits = Field(default_factory=Limits)
    filter_threshold: float = Field(default=0.8, gt=0.5, le=1)
    choice_threshold: float = Field(default=0.6, gt=0, le=1)
    multi_threshold: float = Field(default=0.8, gt=0.5, le=1)

    @model_validator(mode="after")
    def nonempty_questions(self):
        if any(not item.question.strip() for item in self.filter_questions):
            raise ValueError("Filter questions must not be empty")
        return self

    @property
    def filter_questions(self) -> list[Filter]:
        return [Filter(question=item) if isinstance(item, str) else item for item in self.filters]


def load_config(path: Path) -> Config:
    config = Config.model_validate(yaml.safe_load(path.read_text()))
    for source in config.inputs:
        if not source.is_remote:
            local = Path(source.repository).expanduser()
            source.repository = str((path.parent / local).resolve())
    return config
