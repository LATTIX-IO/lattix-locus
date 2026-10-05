"""Shared runtime primitives retained after the legacy package removal."""

from .legacy import alias_legacy_env

# Installs from before the xFrontier -> Locus rename still set FRONTIER_* variables.
alias_legacy_env()
