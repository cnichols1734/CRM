# BOB VTC safety fixtures

These deterministic checks exercise the tool registry and argument sanitizer. They verify that spoofed identity arguments are discarded, Telegram without a selected transaction omits transaction-write tools, and transaction context offers an expected read tool.

The fixture prompts are examples. This suite does not send them to a model, evaluate prompt injection resistance end to end, or prove IDOR protection. Tool dispatch and authorization checks live in `tests/test_bob_tools.py`; route isolation also has dedicated coverage in `tests/test_idor_protection.py`. OpenAI schema serialization is covered in `tests/test_bob_tools.py`.

Run with `.venv/bin/python -m pytest tests/evals -q`. Gold cases live in `fixtures/gold_cases.json`. Keep them synthetic, without real client details. Parameterize only applicable cases instead of collecting skipped placeholders.
