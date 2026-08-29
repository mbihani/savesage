# Prompt provenance

All hashes are SHA-256 of the vendored bytes.

| Bank | Vendored file | Source | SHA-256 |
|---|---|---|---|
| HDFC | `hdfc.txt` | `hdfc/HDFC_PROMPT.txt` | `fd92b25b878176bbb46bed2fd78e8cb5445c7f245ee983d9f8fd01da74ce07ef` |
| ICICI | `icici.txt` | `icici/ICICI_PROMPT.txt` | `8f13a2d35d8b53d7f29f23148912dceb0035ef807165b4c7a54b58d693ca9b2f` |
| SBI | `sbi.txt` | `sbi/SBI_PROMPT.txt` | `b7e06b291803cbcf46bbc6a07af427363d545d3c87d39ee8f64113c8058b3b92` |
| AXIS / GENERIC | `axis.txt` | Bank-neutral `SYSTEM_PROMPT` introduced in `2eef805` | `ca0372008e7623b8a370f7703256812762327abce4a0a1e07c897b58488fd964` |

AXIS and GENERIC share the bank-neutral fallback prompt.
