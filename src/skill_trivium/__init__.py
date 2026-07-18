"""Expose package metadata for the Skill Trivium command-line application.

The version is read from installed package metadata when available, with a
development fallback for source checkouts that have not been installed yet.
"""

from importlib.metadata import PackageNotFoundError, version

_DISTRIBUTION_NAME = "skill-trivium"

try:
    __version__ = version(_DISTRIBUTION_NAME)
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["__version__"]
