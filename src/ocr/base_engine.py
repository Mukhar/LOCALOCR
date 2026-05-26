"""
base_engine.py
~~~~~~~~~~~~~~
Abstract base class for OCR engines in the LOCALOCR pipeline.
All OCR engines must implement this interface.
"""

from abc import ABC, abstractmethod


class OCREngine(ABC):
    """Abstract base class for OCR engines."""

    @abstractmethod
    def recognize(self, image_path: str, languages: list = None) -> str:
        """
        Perform OCR on a single image.

        Parameters
        ----------
        image_path : str
            Absolute path to the image file.
        languages : list[str], optional
            Language codes to recognize (e.g. ['en'], ['hi', 'en']).

        Returns
        -------
        str
            Recognized text.
        """

    @abstractmethod
    def supported_languages(self) -> list:
        """Return list of language codes this engine supports."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the engine name identifier."""
