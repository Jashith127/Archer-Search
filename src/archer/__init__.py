"""Archer: personal AI search engine served on localhost."""

import os

# numpy's OpenBLAS reserves a buffer per CPU thread at import (~350 MB committed on 12 threads).
# Archer's matrices are a few thousand rows of history vectors, which one thread handles as fast.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

__version__ = "0.2.0"
