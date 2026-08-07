# BOB VTC Eval Suite (E0B-5)

De-identified gold fixtures and safety checks for virtual TC quality.

## What this covers

- Tool-selection: given a prompt, expected tools must appear / forbidden tools must not
- Injection: page text / attachment notes must not override org identity
- IDOR: spoofed `entity_id` / `transaction_id` must fail closed

## Run

```bash
.venv/bin/python -m pytest tests/evals -q
```

Gold cases live in `fixtures/gold_cases.json`. Keep payloads de-identified — no real client names, phones, or addresses.
