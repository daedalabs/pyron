from __future__ import annotations

from abc import ABC, abstractmethod

from pyron.api.contracts import EvidenceSpan, Source


class BaseExtractor(ABC):
    @abstractmethod
    def can_handle(self, source: Source) -> bool:
        ...

    @abstractmethod
    def extract(self, source: Source) -> list[EvidenceSpan]:
        ...
