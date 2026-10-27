import sys
from pathlib import Path

# evaluation code imports the backend package `app`
sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
