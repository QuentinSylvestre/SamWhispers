"""Application-specific exceptions."""


class ShutdownRequested(Exception):
    """Raised when a shutdown event interrupts a blocking operation."""
