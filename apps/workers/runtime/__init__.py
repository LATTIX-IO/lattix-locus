try:
    import locus_runtime  # noqa: F401  (aliases legacy FRONTIER_* env vars)
except ImportError:  # workers image without the shared runtime package
    pass

__all__ = []
