"""Platform adapter base class."""
import os
from abc import ABC, abstractmethod


class PlatformAdapter(ABC):
    """Each social platform implements this interface."""

    name: str = "base"
    kind: str = "organic"   # "organic" or "paid"
    env_vars: list = []     # which .env vars are required

    def credentials_ok(self) -> tuple:
        """Return (bool, list_of_missing_vars)."""
        missing = [v for v in self.env_vars if not os.environ.get(v)]
        return (len(missing) == 0, missing)

    def status(self) -> dict:
        ok, missing = self.credentials_ok()
        return {
            "platform": self.name,
            "kind": self.kind,
            "live": ok,
            "missing_env_vars": missing,
        }

    @abstractmethod
    def post(self, formatted: dict, dry_run: bool = False) -> dict:
        """Post the given formatted content. Returns {'status': 'posted'|'dry_run'|'failed', ...}."""
        ...
