"""Multi-stage optimizer package.

Each stage is intentionally implemented in its own module file.
"""

from src.app.infrastructure.optimizer.multistage.pipeline import (
    run_multistage_optimization,
)
from src.app.infrastructure.optimizer.multistage.shared import (
    Stage1Config,
    Stage2Config,
    Stage3Config,
)
from src.app.infrastructure.optimizer.multistage.stage1 import (
    optimize_pure_astronomical_core,
)
