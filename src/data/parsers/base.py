"""Abstract base class for all data parsers."""
from abc import ABC, abstractmethod
from ..session import Session


class BaseParser(ABC):
    """Parse a file into a Session object."""

    @abstractmethod
    def can_parse(self, path: str) -> bool:
        """Return True if this parser can handle the given file."""

    @abstractmethod
    def parse(self, path: str) -> Session:
        """Parse the file and return a populated Session."""
