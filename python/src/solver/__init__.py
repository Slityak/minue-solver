"""IPython extension providing the %%solve cell magic.

Usage in Colab:
    !pip install -q --force-reinstall --no-deps git+https://github.com/Slityak/minue-solver.git#subdirectory=python
    %load_ext solver
"""

from .client import SolveResult, SolverClient, SolverConfigError, SolverRequestError
from .magic import SolverMagics

__all__ = [
    "SolveResult",
    "SolverClient",
    "SolverConfigError",
    "SolverRequestError",
    "load_ipython_extension",
]


def load_ipython_extension(ipython) -> None:
    """Called by %load_ext solver. Re-loading simply re-registers the magic."""
    ipython.register_magics(SolverMagics(ipython))
