"""Step 10 — schema-guided structured output.

Forces LLM output to conform to ``ontology/v1_2_schema.json``. The MVP uses two
paths: Ollama's native ``format`` JSON-Schema constraint, and (optionally) the
``outlines`` library for stricter token-level enforcement — see DECISIONS D-004.
Generation is deferred until a local LLM is wired in — see DECISIONS D-003.
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


def validate_against_schema(payload: dict[str, object]) -> ChangeEvent:
    """Validate an LLM payload through the ChangeEvent Pydantic model.

    Args:
        payload: A candidate ChangeEvent dict produced by an LLM.

    Returns:
        A validated :class:`ChangeEvent`.

    Raises:
        pydantic.ValidationError: If the payload violates the schema/axioms.
    """
    return ChangeEvent.model_validate(payload)


def generate_change_event(prompt: str, context: str) -> ChangeEvent:
    """Generate a schema-conformant ChangeEvent from a prompt and context.

    Args:
        prompt: The user request.
        context: Retrieved grounding context.

    Returns:
        A validated :class:`ChangeEvent`.

    Raises:
        NotImplementedError: Deferred — requires a local LLM (D-003). Wire this
            to Ollama ``format=<schema>`` or ``outlines``, then pass the result
            through :func:`validate_against_schema`.
    """
    # TODO(real-data): call the local LLM with the v1.2 schema as the output
    # constraint, then validate via validate_against_schema().
    raise NotImplementedError("Schema-guided generation deferred (D-003).")
