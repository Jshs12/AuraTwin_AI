"""Embedding interface and explicitly non-semantic local development encoder."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    name: str
    def embed(self, text: str) -> list[float]: ...


class DevelopmentHashingEmbeddingProvider:
    """Stable feature hashing for plumbing/tests; it does not encode meaning."""
    name = "development_hashing_not_semantic"

    def __init__(self, dimensions: int = 64):
        if dimensions < 8:
            raise ValueError("Embedding dimensions must be at least 8")
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[\w-]+", text.casefold()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
