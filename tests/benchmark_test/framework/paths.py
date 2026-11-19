"""Where the benchmark suite keeps its own data (worlds and recall cases ship in the package)."""

from pathlib import Path

TEST_DATA = Path(__file__).resolve().parents[1] / "data"
