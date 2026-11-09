"""Loads every map JSON and answers 'which map is this text / log token?'."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import data_dir
from .map_model import MapData

log = logging.getLogger(__name__)

try:  # rapidfuzz is a hard dependency, but keep a stdlib fallback for minimal envs
    from rapidfuzz import fuzz

    def _score(a: str, b: str) -> float:
        return float(max(fuzz.ratio(a, b), fuzz.token_set_ratio(a, b) * 0.97, fuzz.partial_ratio(a, b) * 0.9))

except ImportError:  # pragma: no cover
    import difflib

    def _score(a: str, b: str) -> float:
        return 100.0 * difflib.SequenceMatcher(None, a, b).ratio()


_OCR_DIGIT_FIX = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "|": "l"})
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_name(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.casefold().replace("’", "'").replace("'", "")
    t = _NON_ALNUM.sub(" ", t)
    t = " ".join(t.split())
    return t.removeprefix("the ")


@dataclass(frozen=True, slots=True)
class NameMatch:
    map_id: str
    score: float  # 0..100
    matched_alias: str


class MapRegistry:
    def __init__(self, maps: dict[str, MapData]) -> None:
        self._maps = dict(maps)
        self._alias_index: list[tuple[str, str, str]] = []  # (normalized, map_id, original)
        self._log_index: dict[str, str] = {}
        for m in self._maps.values():
            for name in m.all_names():
                self._alias_index.append((normalize_name(name), m.id, name))
            for token in m.log_ids:
                self._log_index[token.casefold()] = m.id

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, root: Path | None = None, *, include_test_maps: bool = True) -> MapRegistry:
        root = root or data_dir() / "maps"
        maps: dict[str, MapData] = {}
        for path in sorted(root.rglob("*.json")):
            if path.name.startswith("_"):
                continue  # _index.json and other metadata
            if not include_test_maps and "_test" in path.relative_to(root).parts:
                continue
            try:
                m = MapData.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                raise ValueError(f"invalid map file {path}: {exc}") from exc
            if m.id in maps:
                raise ValueError(f"duplicate map id {m.id} ({path})")
            maps[m.id] = m
        log.info("loaded %d maps from %s", len(maps), root)
        return cls(maps)

    # ------------------------------------------------------------------ queries
    def __len__(self) -> int:
        return len(self._maps)

    def __contains__(self, map_id: object) -> bool:
        return map_id in self._maps

    def get(self, map_id: str) -> MapData:
        return self._maps[map_id]

    def all(self) -> list[MapData]:
        return list(self._maps.values())

    def available(self) -> list[MapData]:
        return [m for m in self._maps.values() if m.available and m.status != "synthetic"]

    def by_log_token(self, token: str) -> str | None:
        return self._log_index.get(token.casefold())

    def match_text(self, text: str, *, limit: int = 3, min_score: float = 60.0) -> list[NameMatch]:
        """Fuzzy-match OCR'd text (any language present in aliases) to map ids."""
        q = normalize_name(text)
        if not q:
            return []
        variants = {q, normalize_name(text.translate(_OCR_DIGIT_FIX))}
        best: dict[str, NameMatch] = {}
        for norm, map_id, original in self._alias_index:
            s = max(_score(v, norm) for v in variants)
            if s >= min_score and (map_id not in best or s > best[map_id].score):
                best[map_id] = NameMatch(map_id, s, original)
        return sorted(best.values(), key=lambda m: -m.score)[:limit]
