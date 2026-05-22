"""Step 10 — schema-guided structured output (interface skeleton).

Forces LLM output to conform to ``ontology/v1_2_schema.json`` via OpenAI
Structured Outputs (pure HTTP — the ``outlines`` library is intentionally not
used; see DECISIONS D-004). Implementation is deferred until an LLM API key and
real data are available — see DECISIONS D-003.
"""

from __future__ import annotations

import json

from ontology.models import ChangeEvent
from src.utils.paths import V1_2_SCHEMA_PATH


def load_output_schema() -> dict[str, object]:
    """Load the v1.2 JSON Schema used to constrain LLM output.

    Returns:
        The parsed JSON Schema dict.
    """
    return json.loads(V1_2_SCHEMA_PATH.read_text(encoding="utf-8"))


def generate_change_event(prompt: str, context: str) -> ChangeEvent:
    """Generate a schema-conformant ChangeEvent from a prompt and context.

    Args:
        prompt: The user request.
        context: Retrieved grounding context.

    Returns:
        A validated :class:`ChangeEvent`.

    Raises:
        NotImplementedError: Always — implementation deferred (D-003).
    """
    # TODO(real-data): call the LLM with response_format set to the v1.2 schema,
    # then validate the result through the ChangeEvent Pydantic model.
    raise NotImplementedError("Schema-guided generation deferred (D-003).")
