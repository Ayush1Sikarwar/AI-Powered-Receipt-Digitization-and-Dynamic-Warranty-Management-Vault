# Warranty Mapping Evaluation (rules engine)

- Engine: **rule-based warranty matching (find_warranty_policy) + timeline rules (compute_timeline)** (live code)
- Dataset: deterministic synthetic policy catalog (7 cases: region priority, versioning, generic fallback, registration UNKNOWN, registration anchored, battery coverage, out-of-window no-match)

| Metric | Value |
| --- | --- |
| policy correctness | 1.0 |
| duration correctness (expiry date or honest UNKNOWN) | 1.0 |
| source validity (mapped source = expected valid source) | 1.0 |

Per case:

| case | policy ok | source ok | duration ok |
| --- | --- | --- | --- |
| IN purchase → current v2.0, 120-month compressor | PASS | PASS | PASS |
| US purchase → region-specific policy | PASS | PASS | PASS |
| Unknown region + region-specific policies only → NO mapping (honest) | PASS | PASS | PASS |
| Registration required, not recorded → honest UNKNOWN (never guessed) | PASS | PASS | PASS |
| Registration recorded → 24 months from registration date | PASS | PASS | PASS |
| Laptop → battery + unit coverage | PASS | PASS | PASS |
| Purchase before effective window → NO policy (no fabrication) | PASS | PASS | PASS |

Honest caveats: synthetic policy catalog with hand-derived expectations; real manufacturer
term tables (effective windows, regional overrides, component exceptions) will shift these
numbers. The honesty cases (UNKNOWN / no-match) are the strongest signal for the
'never guess a warranty duration' constraint.