"""Error types for gitdeck."""


class GitDeckError(Exception):
    """Raised for any recoverable failure in a gitdeck operation.

    Every public function raises this (and only this) on failure so callers --
    including the CLI and the GUI -- have a single exception to catch and can
    show the user a clear message instead of a raw dulwich traceback.
    """
