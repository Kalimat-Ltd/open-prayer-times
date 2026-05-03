from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
REFERENCE_DIR = PROJECT_ROOT / "reference"
RESOURCES_DIR = PROJECT_ROOT / "resources"
LOC_CSV_PATH = RESOURCES_DIR / "locations.csv"
