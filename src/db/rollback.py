"""D-012 Phase 2 stub — file_id-based rollback lands in Phase 5.

run_id-based rollback is gone (no preprocessing_runs table in the new schema).
The new ``rollback_file(session, file_id)`` cascades through source_files →
ingestion_log → dev_part_master via ``ON DELETE CASCADE``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class RollbackResult:
    file_id: int | None = None
    rows_deleted: dict[str, int] = field(default_factory=dict)


def rollback_file(*args: Any, **kwargs: Any) -> RollbackResult:
    raise NotImplementedError(
        "src.db.rollback.rollback_file is being implemented in Phase 5."
    )


# Keep the old name importable until Phase 5 / Phase 6 rewrites the CLI.
def rollback_run(*args: Any, **kwargs: Any) -> RollbackResult:
    raise NotImplementedError(
        "rollback_run was removed with the run_id-based lifecycle. "
        "Use rollback_file(session, file_id) once Phase 5 ships."
    )
