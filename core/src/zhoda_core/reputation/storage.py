"""Persistent storage for the domain ELO matrix.

JSON with atomic replace; path resolution order:
explicit path > $ZHODA_REPUTATION_PATH > ~/.zhoda/reputation.json
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional, Union

from .matrix import DomainEloMatrix

_ENV_VAR = "ZHODA_REPUTATION_PATH"
_DEFAULT_PATH = Path.home() / ".zhoda" / "reputation.json"


class ReputationStorage:
    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        if path is None:
            path = os.environ.get(_ENV_VAR) or _DEFAULT_PATH
        self.path = Path(path)

    def load(self) -> DomainEloMatrix:
        if not self.path.exists():
            return DomainEloMatrix()
        with self.path.open("r", encoding="utf-8") as fh:
            return DomainEloMatrix.from_dict(json.load(fh))

    def save(self, matrix: DomainEloMatrix) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(matrix.to_dict(), fh, indent=2, sort_keys=True)
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return self.path
