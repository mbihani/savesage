"""Run/session memory retained while a statement moves through the graph."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from contracts.models import Bank


@dataclass(slots=True)
class SessionMemory:
    statement_id: str
    request_id: str
    bank: Bank


class MemoryStore(ABC):
    """Framework-neutral session storage; workstream 2 wires graph state to it."""

    @abstractmethod
    def read(self, request_id: str) -> SessionMemory | None:
        raise NotImplementedError

    @abstractmethod
    def write(self, memory: SessionMemory) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, request_id: str) -> None:
        raise NotImplementedError
