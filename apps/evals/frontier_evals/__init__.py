"""frontier-evals — evaluate the xFrontier coding agent on SWE benchmarks.

Drives ``frontier_runtime.harness.SweAgent`` over a dataset of instances,
grades each produced patch by *test execution* (never patch plausibility),
runs multiple seeds, and reports the mean resolve rate +/- SEM
(SWE-rebench protocol).

Two modes:
* ``plumbing`` — synthetic in-repo tasks, runs anywhere (CI), no GPU/Docker.
* ``live``     — real SWE-bench instances in Docker against a model served by
  vLLM/llama.cpp on a remote runner. Refuses to run a fleet against localhost.
"""

from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
