import json
import re
from collections.abc import Iterable


SOURCE_LABELS = {
    "pararius": "Pararius",
    "funda": "Funda",
    "kamernet": "Kamernet",
    "huurwoningen": "Huurwoningen",
    "roofz": "Roofz",
}
ALL_SOURCES = tuple(SOURCE_LABELS)
DEFAULT_ENABLED_SOURCES_JSON = json.dumps(list(ALL_SOURCES))

SOURCE_ALIASES = {
    "pararius": "pararius",
    "pararius.nl": "pararius",
    "funda": "funda",
    "funda.nl": "funda",
    "kamernet": "kamernet",
    "kamernet.nl": "kamernet",
    "huurwoningen": "huurwoningen",
    "huurwoningen.nl": "huurwoningen",
    "huur": "huurwoningen",
    "roofz": "roofz",
    "roofz.eu": "roofz",
}


def normalize_source(value: str) -> str | None:
    return SOURCE_ALIASES.get(value.strip().casefold())


def parse_source_tokens(tokens: Iterable[str]) -> tuple[list[str], list[str]]:
    sources: list[str] = []
    invalid: list[str] = []
    for token in _split_tokens(tokens):
        source = normalize_source(token)
        if source is None:
            invalid.append(token)
        elif source not in sources:
            sources.append(source)
    return sources, invalid


def normalize_sources(value=None) -> tuple[str, ...]:
    if value is None:
        return ALL_SOURCES

    raw_sources = _coerce_source_values(value)
    selected = []
    for item in raw_sources:
        source = normalize_source(str(item))
        if source and source not in selected:
            selected.append(source)

    if not selected:
        return ALL_SOURCES
    return tuple(source for source in ALL_SOURCES if source in selected)


def serialize_sources(value=None) -> str:
    return json.dumps(list(normalize_sources(value)))


def format_sources(value=None) -> str:
    return ", ".join(SOURCE_LABELS[source] for source in normalize_sources(value))


def _coerce_source_values(value) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError:
                return _split_text(stripped)
            if isinstance(loaded, list):
                return [str(item) for item in loaded]
            return []
        return _split_text(stripped)

    if isinstance(value, Iterable):
        return [str(item) for item in value]

    return [str(value)]


def _split_tokens(tokens: Iterable[str]) -> list[str]:
    return _split_text(" ".join(str(token) for token in tokens))


def _split_text(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,;]+", value.strip()) if part]
