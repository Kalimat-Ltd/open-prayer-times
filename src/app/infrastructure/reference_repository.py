from pathlib import Path
from typing import Dict, List, Tuple

from src.app.infrastructure.reference_parser import load_reference_file


def load_reference_times(reference_file: Path) -> Tuple[Dict, List]:
    return load_reference_file(reference_file)
