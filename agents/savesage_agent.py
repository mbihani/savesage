"""Baseline Savesage ICICI extraction agent (code-mode).

The ANVIL optimizer mutates this file to reduce Luna latency by tuning
reasoning_effort / max_tokens per statement. The baseline uses the
production defaults (medium reasoning effort, 96K max tokens).
"""

from anvil.domains.savesage.agent_base import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_REASONING_EFFORT,
    SavesageAgent,
)


class BaselineSavesageAgent(SavesageAgent):
    """Baseline: production defaults for every statement."""

    def predict(self, pdf_path: str) -> tuple[dict, float]:
        return self.run_luna(
            pdf_path,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
