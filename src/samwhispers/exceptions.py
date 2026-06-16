"""Application-specific exceptions."""


class ShutdownRequested(Exception):
    """Raised when a shutdown event interrupts a blocking operation."""


class StreamingUnavailableError(Exception):
    """Raised when the whisper server cannot provide word timestamps."""
