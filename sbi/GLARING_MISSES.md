## The glaring misses

Substantive errors vs the Opus-5 GT, excluding 55 structurally-asymmetric `utilisationPercent` cells and the GT-instrument artifacts listed below.

- **Luna (refined): 173 substantive errors (9 in the 16 priority fields), affecting 9 statements**
- **Incumbent CSV: 843 substantive errors (17 in the 16 priority fields), affecting 13 statements**

GT-instrument artifacts excluded (verified GT defects, NOT challenger errors):

| artifact | count |
|---|---:|
| luna:GT_READ_SECTION_HEADER_AS_PROGRAMTYPE | 61 |
| luna:GT_HAS_NO_NONDATE_DUEDATE_RULE | 16 |
| luna:GT_LEFT_CONTINUATION_ROW_DATE_NULL | 21 |
| incumbent:GT_READ_SECTION_HEADER_AS_PROGRAMTYPE | 61 |
| incumbent:GT_HAS_NO_NONDATE_DUEDATE_RULE | 16 |
| incumbent:GT_LEFT_CONTINUATION_ROW_DATE_NULL | 30 |

### Luna (refined) — every substantive error

| field | n | statements |
|---|---:|---|
| `transactions[].txnType` | 106 | 119535901, 129215339, 142194871, 142826009, 146930601, 172420705, 186548429, 199386, 202738690, 234547848, 243926575, 247847647, 255661827, 264695036 … |
| `cards[].cardMeta.productFamily` | 23 | 1162118, 142194871, 186473748, 190790537, 199386, 221159806, 223400342, 226707778, 230215053, 234547848, 243926575, 263555998, 273593709, 280105657 … |
| `rewards.pointsEarnedThisCycle` | 10 | 243926575, 248267921, 260493437, 284029679, 318267391, 341705711, 361589519, 369606524, 393366914, 838411 |
| `rewards.pointsExpiringNext60Days` | 10 | 1132035, 155642305, 186548429, 226707778, 264695036, 273596610, 273650193, 315786283, 316218178, 325774041 |
| `rewards.programType` | 8 | 186473748, 243926575, 248267921, 260493437, 318267391, 341705711, 369606524, 838411 |
| `rewards.pointsExpiringNext30Days` | 5 | 186548429, 264695036, 273650193, 311317506, 316218178 |
| `statementMeta.issuerName` | 3 | 198888784, 248267921, 263555998 |
| `transactions[].direction` | 2 | 162725042, 273593709 |
| `rewards.closingPoints` | 2 | 248267921, 341705711 |
| `cards[].cardMeta.network` | 2 | 264695036, 400830575 |
| `cards[].cardMeta.cardDisplayName` | 1 | 361589519 |
| `transactions[].description` | 1 | 369606524 |

**Priority-field errors, itemised with PDF evidence:**

- **162725042** `transactions[].direction` — luna=`'DEBIT'` vs GT=`'CREDIT'` (wrong_value)  
  PDF: `[{"page": 1, "date": "17 Oct 25", "amount_printed": "5,114.00", "marker": null, "desc": "TRANSFER TO MERCHANT EMI"}]`
- **198888784** `statementMeta.issuerName` — luna=`'SBI Card'` vs GT=`'SBI Cards and Payment Services Limited (SBI Card)'` (wrong_value)
- **248267921** `statementMeta.issuerName` — luna=`'SBI Card'` vs GT=`'SBI Cards and Payment Services Limited (SBI Card)'` (wrong_value)
- **263555998** `statementMeta.issuerName` — luna=`'SBI Card'` vs GT=`'SBI Cards and Payment Services Limited (SBI Card)'` (wrong_value)
- **264695036** `cards[].cardMeta.network` — luna=`'VISA'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 3, "rect": [324.4, 759.9, 342.6, 771.1], "line": "Conditions\u201d & updated information on all ongoing offers."}, {"page": 8, "rect": [363.8, 1236.5, 387.1, 1249.9], "line": "`
- **273593709** `transactions[].direction` — luna=`'DEBIT'` vs GT=`'CREDIT'` (wrong_value)  
  PDF: `[{"page": 1, "date": "06 Jan 26", "amount_printed": "46,000.00", "marker": null, "desc": "TRANSFER TO FLEXIPAY INSTALLMENT"}]`
- **361589519** `cards[].cardMeta.cardDisplayName` — luna=`'Tata Neu Infinity SBI Card'` vs GT=`'Tata Neu Infinity SBI Credit Card'` (wrong_value)
- **369606524** `transactions[].description` — luna=`'Cashfree*FLIPKART Bengaluru IND'` vs GT=`'Cashfree*FLIPKART INTE Bengaluru    IND'` (wrong_value)  
  PDF: `[{"page": 1, "date": "28 May 26", "amount_printed": "1,336.00", "marker": "D", "desc": "Cashfree*FLIPKART INTE Bengaluru IND"}]`
- **400830575** `cards[].cardMeta.network` — luna=`'VISA'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 7, "rect": [363.7, 1217.0, 387.1, 1230.5], "line": "made by customer through any instant channel (NEFT, Visa Money Transfer, MasterCard"}], "verdict": "printed"}`

### Incumbent CSV — every substantive error

| field | n | statements |
|---|---:|---|
| `transactions[].txnType` | 704 | 1132035, 1162118, 119535901, 129215339, 142194871, 142826009, 146930601, 148036763, 155642305, 162725042, 172420705, 179943460, 186473748, 186548429 … |
| `statementMeta.rawStatementId` | 76 | 1132035, 1162118, 119535901, 129215339, 142194871, 142826009, 146930601, 148036763, 155642305, 162725042, 172420705, 179943460, 186473748, 186548429 … |
| `rewards.pointsExpiringNext30Days` | 18 | 148036763, 155642305, 186548429, 198888784, 215155650, 221159806, 223400342, 226707778, 230215053, 273593709, 273596610, 273613913, 273650193, 273706322 … |
| `rewards.programType` | 8 | 186473748, 243926575, 248267921, 260493437, 318267391, 341705711, 369606524, 838411 |
| `rewards.pointsEarnedThisCycle` | 8 | 243926575, 248267921, 260493437, 318267391, 341705711, 369606524, 393366914, 838411 |
| `cards[].cardMeta.network` | 7 | 221159806, 223400342, 319897605, 320134, 325833538, 363063468, 400830575 |
| `rewards.closingPoints` | 6 | 186473748, 243926575, 260493437, 318267391, 369606524, 838411 |
| `transactions[].amount` | 5 | 186548429, 273593709 |
| `rewards.pointsExpiringNext60Days` | 4 | 291528552, 311317506, 315786283, 325774041 |
| `statementMeta.issuerName` | 3 | 198888784, 248267921, 263555998 |
| `cards[].cardMeta.cardDisplayName` | 2 | 221159806, 361589519 |
| `rewards.pointsRedeemedThisCycle` | 2 | 243926575, 393366914 |

**Priority-field errors, itemised with PDF evidence:**

- **320134** `cards[].cardMeta.network` — incumbent=`'VISA'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 7, "rect": [363.8, 1236.5, 387.1, 1249.9], "line": "made by customer through any instant channel (NEFT, Visa Money Transfer, MasterCard"}], "verdict": "printed"}`
- **186548429** `transactions[].amount` — incumbent=`-31595.35` vs GT=`31595.35` (wrong_value)  
  PDF: `[{"page": 1, "date": "05 Aug 25", "amount_printed": "31,595.35", "marker": null, "desc": "TRANSFER TO MERCHANT EMI"}]`
- **198888784** `statementMeta.issuerName` — incumbent=`'SBI Card'` vs GT=`'SBI Cards and Payment Services Limited (SBI Card)'` (wrong_value)
- **221159806** `cards[].cardMeta.cardDisplayName` — incumbent=`'BIPIN PATEL'` vs GT=`'BPCL SBI Card OCTANE'` (wrong_value)
- **221159806** `cards[].cardMeta.network` — incumbent=`'RuPay'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 1, "non_boilerplate_hits": [], "verdict": "HALLUCINATION - only boilerplate"}`
- **223400342** `cards[].cardMeta.network` — incumbent=`'VISA'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 6, "rect": [494.7, 717.1, 522.4, 730.6], "line": "Emergency Card Replacement (When"}, {"page": 7, "rect": [363.7, 1216.1, 387.1, 1229.6], "line": "made by customer through any `
- **248267921** `statementMeta.issuerName` — incumbent=`'SBI Card'` vs GT=`'SBI Cards and Payment Services Limited (SBI Card)'` (wrong_value)
- **263555998** `statementMeta.issuerName` — incumbent=`'SBI Card'` vs GT=`'SBI Cards and Payment Services Limited (SBI Card)'` (wrong_value)
- **273593709** `transactions[].amount` — incumbent=`-499` vs GT=`499.0` (wrong_value)  
  PDF: `[{"page": 1, "date": "11 Dec 25", "amount_printed": "499.00", "marker": "C", "desc": "SBR ANNUAL FEE REVERSA(EXCL TAX 89.82)"}]`
- **273593709** `transactions[].amount` — incumbent=`-20.1` vs GT=`20.1` (wrong_value)  
  PDF: `[{"page": 1, "date": "27 Dec 25", "amount_printed": "20.10", "marker": "C", "desc": "FUEL SURCHARGE WAIVER EXCL TAX"}]`
- **273593709** `transactions[].amount` — incumbent=`-3682` vs GT=`3682.0` (wrong_value)  
  PDF: `[{"page": 1, "date": "30 Dec 25", "amount_printed": "3,682.00", "marker": "C", "desc": "PAYMENT RECEIVED 000DP01536411493705Y9Vh"}]`
- **273593709** `transactions[].amount` — incumbent=`-46000` vs GT=`46000.0` (wrong_value)  
  PDF: `[{"page": 1, "date": "06 Jan 26", "amount_printed": "46,000.00", "marker": null, "desc": "TRANSFER TO FLEXIPAY INSTALLMENT"}]`
- **319897605** `cards[].cardMeta.network` — incumbent=`None` vs GT=`'VISA'` (null_when_populated)  
  PDF: `{"literal_hits": 0, "non_boilerplate_hits": [], "verdict": "printed"}`
- **325833538** `cards[].cardMeta.network` — incumbent=`'Visa'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 7, "rect": [363.7, 1216.1, 387.1, 1229.6], "line": "made by customer through any instant channel (NEFT, Visa Money Transfer, MasterCard"}], "verdict": "printed"}`
- **361589519** `cards[].cardMeta.cardDisplayName` — incumbent=`'Tata Neu Infinity SBI Card'` vs GT=`'Tata Neu Infinity SBI Credit Card'` (wrong_value)
- **363063468** `cards[].cardMeta.network` — incumbent=`'VISA'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 7, "rect": [363.7, 1216.1, 387.1, 1229.6], "line": "made by customer through any instant channel (NEFT, Visa Money Transfer, MasterCard"}], "verdict": "printed"}`
- **400830575** `cards[].cardMeta.network` — incumbent=`'VISA'` vs GT=`None` (hallucinated_when_null)  
  PDF: `{"literal_hits": 5, "non_boilerplate_hits": [{"page": 7, "rect": [363.7, 1217.0, 387.1, 1230.5], "line": "made by customer through any instant channel (NEFT, Visa Money Transfer, MasterCard"}], "verdict": "printed"}`