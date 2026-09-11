from dataclasses import dataclass
import math
import os
from .env import load_env


@dataclass(frozen=True)
class Settings:
    capacity: int = 20
    failure_rate: float = 0.05
    jitter_fraction: float = 0.10
    seed: str = "development-seed"
    retry_after_seconds: int = 1
    db_path: str = "state/processor.sqlite3"
    admin_token: str = "local-admin-change-me"
    signing_key: str = "local-signing-key-not-for-evaluation"
    mode: str = "development"

    def __post_init__(self):
        if not 1 <= self.capacity <= 4096:
            raise ValueError("capacity must be between 1 and 4096")
        if not math.isfinite(self.failure_rate) or not 0 <= self.failure_rate <= 1:
            raise ValueError("failure_rate must be between 0 and 1")
        if not math.isfinite(self.jitter_fraction) or not 0 <= self.jitter_fraction <= 0.5:
            raise ValueError("jitter_fraction must be between 0 and 0.5")
        if not 1 <= self.retry_after_seconds <= 60:
            raise ValueError("retry_after_seconds must be between 1 and 60")
        if not self.seed or not self.admin_token or not self.signing_key:
            raise ValueError("seed, admin token and signing key must be nonempty")
        if self.mode not in {"development", "evaluation"}:
            raise ValueError("mode must be development or evaluation")
        if self.mode == "evaluation" and (
            self.admin_token == "local-admin-change-me"
            or self.signing_key == "local-signing-key-not-for-evaluation"
        ):
            raise ValueError("evaluation requires non-default admin and signing secrets")

    @classmethod
    def from_env(cls):
        load_env()
        return cls(
            capacity=int(os.getenv("PROCESSOR_CAPACITY", "20")),
            failure_rate=float(os.getenv("PROCESSOR_FAILURE_RATE", "0.05")),
            jitter_fraction=float(os.getenv("PROCESSOR_JITTER_FRACTION", "0.10")),
            seed=os.getenv("PROCESSOR_SEED", "development-seed"),
            retry_after_seconds=int(os.getenv("PROCESSOR_RETRY_AFTER_SECONDS", "1")),
            db_path=os.getenv("PROCESSOR_DB", "state/processor.sqlite3"),
            admin_token=os.getenv("PROCESSOR_ADMIN_TOKEN", "local-admin-change-me"),
            signing_key=os.getenv("PROCESSOR_SIGNING_KEY", "local-signing-key-not-for-evaluation"),
            mode=os.getenv("PROCESSOR_MODE", "development"),
        )
