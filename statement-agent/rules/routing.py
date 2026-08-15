"""Per-bank prompt routing; AXIS intentionally uses the generic Luna prompt."""

from pathlib import Path

from contracts.models import Bank

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_BY_BANK: dict[Bank, Path] = {
    Bank.HDFC: PROMPT_DIR / "hdfc.txt",
    Bank.ICICI: PROMPT_DIR / "icici.txt",
    Bank.SBI: PROMPT_DIR / "sbi.txt",
    Bank.AXIS: PROMPT_DIR / "axis.txt",
}
