"""Shared cancellation signal between job orchestration and TTS engines."""


class GenerationAborted(Exception):
    """Raised from an ``on_item`` callback to stop a running generation.

    Engines must let this exception propagate instead of logging it, so that a
    cancelled job stops after the current GPU pack rather than finishing the
    whole chapter.
    """


class SiblingAborted(GenerationAborted):
    """A parallel worker stopped because another worker was cancelled or failed."""
