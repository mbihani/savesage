"""PII handling constraints applied to logs, traces, errors, and fixtures."""

PII_RULES = (
    "Never log or trace PDF bytes or base64 PDF content.",
    "Never persist a full card number; retain only an extracted lastFourDigit.",
    "Redact cardholder names and statement identifiers from diagnostic text.",
    "Never commit PDFs, CSVs, parsed statement JSON, or production responses.",
    "Fixtures must be synthetic, conspicuously labelled, and contain fake values.",
)
