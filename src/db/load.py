"""D-012 Phase 2 stub — full rewrite arrives in Phase 5.

The dev_part_master loader lives here. Phase 2 (this commit) only swaps the
ORM out, so this module is intentionally a stub: it lets ``src.db`` and the
CLI import cleanly while the new transactional loader is implemented in
Phase 5. Any actual load call raises ``NotImplementedError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class LoadResult:
    """Loader result placeholder — will be repopulated in Phase 5."""

    file_id: int | None = None
    rows_inserted: dict[str, int] = field(default_factory=dict)


def load_run(*args: Any, **kwargs: Any) -> LoadResult:
    raise NotImplementedError(
        "src.db.load.load_run is being rewritten for dev_part_master in Phase 5."
    )


def update_embeddings(*args: Any, **kwargs: Any) -> int:
    raise NotImplementedError(
        "src.db.load.update_embeddings is being rewritten in Phase 5."
    )


def load_to_db(*args: Any, **kwargs: Any) -> LoadResult:
    raise NotImplementedError(
        "src.db.load.load_to_db is being rewritten in Phase 5."
    )
