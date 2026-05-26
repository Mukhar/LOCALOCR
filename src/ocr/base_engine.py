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

    @property
    def max_parallel_frames(self) -> int:
        """
        Maximum number of frames that can be OCR'd concurrently by this engine.

        Override in engines that support parallelism. Defaults to 1 (sequential).
        """
        return 1

    @property
    def supports_multiprocessing(self) -> bool:
        """
        Whether this engine can run in separate worker processes via
        ProcessPoolExecutor(spawn). Engines that can be re-initialised from
        a plain config dict should override this to True.

        Multiprocessing bypasses the Python GIL and the PyObjC bridge
        serialisation, enabling true hardware parallelism (e.g. ANE).
        Default False (safe for all engines).
        """
        return False

    def worker_init_args(self) -> dict:
        """
        Return the kwargs needed to reconstruct this engine in a worker process.
        Only used when supports_multiprocessing is True.
        """
        return {}
