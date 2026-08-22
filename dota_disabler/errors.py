"""Expected application errors shared by all adapters."""


class GeneratorError(RuntimeError):
    """Base error for an expected generator failure."""


class UnsafeOutputError(GeneratorError):
    """Raised when an output directory is not demonstrably owned by this tool."""


__all__ = ["GeneratorError", "UnsafeOutputError"]
