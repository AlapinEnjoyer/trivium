from importlib.metadata import PackageNotFoundError, version

_DISTRIBUTION_NAME = "skill-trivium"

try:
    __version__ = version(_DISTRIBUTION_NAME)
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = ["__version__"]
