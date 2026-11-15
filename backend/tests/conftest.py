import sys
from pathlib import Path

# Make backend/src importable the same way it is inside the Lambda package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
