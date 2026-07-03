"""
src.context
~~~~~~~~~~~
Context-window expansion for matched OCR results.

Given anchor matches produced by ``text_matcher.match_text``, ``expand_context_windows``
synthesizes context entries for the ±N neighboring frames around each anchor so
downstream steps (organizer, Ollama analyzer) can preserve on-screen context that
was not itself an OCR match.
"""

from .context_expander import expand_context_windows

__all__ = ["expand_context_windows"]
