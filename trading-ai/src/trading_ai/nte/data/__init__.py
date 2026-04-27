import sys
from pathlib import Path

# Add src directory to Python path for module imports
src_path = Path(__file__).resolve().parents[3]
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from trading_ai.nte.data.feature_engine import FeatureSnapshot, compute_features

__all__ = ["FeatureSnapshot", "compute_features"]
