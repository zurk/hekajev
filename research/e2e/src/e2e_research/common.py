"""Shared contracts, deterministic artifacts and the frozen study's definitions."""

import calendar
import gzip
import hashlib
import io
import json
import platform
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

SCHEMA_VERSION = 1
FEATURE_VERSION = "e2e-v1"
LABELS = ("coverage", "product_adaptation", "flakiness", "environment", "refactoring", "test_bug")
NAMES = (
    "Новое покрытие",
    "Адаптация к продукту",
    "Нестабильность",
    "Окружение",
    "Рефакторинг",
    "Ошибка теста",
)
MAINTENANCE = set(LABELS) - {"coverage"}
WINDOWS = (14, 30, 90)
DAY = 86400
BOOTSTRAPS = 10000
SEED = 42
CUTOFF = datetime(2026, 9, 21, 23, 59, 59, tzinfo=UTC)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def anniversary(birth: datetime, year: int) -> datetime:
    return birth.replace(year=year, day=min(birth.day, calendar.monthrange(year, birth.month)[1]))


def age_year(when: datetime, birth: datetime) -> int:
    if when < birth:
        raise ValueError("Commit date predates the repository origin")
    return when.year - birth.year + int(when >= anniversary(birth, when.year))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def code_digest() -> str:
    h = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".py", ".js", ".css", ".html"}:
            continue
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def runtime_versions() -> dict:
    return {
        "python": platform.python_version(),
        "numpy": version("numpy"),
        "hekajev": version("hekajev"),
    }


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def read_jsonl(path: Path) -> Iterator[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    """Stable gzip headers make byte hashes independent of wall time and destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("wb") as raw:
        compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
        with io.TextIOWrapper(compressed, encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
                count += 1
    temporary.replace(path)
    return count
