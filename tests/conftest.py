import sys
from pathlib import Path

# Make the repo root importable so `import backtest` works from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
