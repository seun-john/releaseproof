# Contributing

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q
```

Keep the standard library as the only runtime dependency unless there is a strong reason.
Every behaviour change needs a test, and every limit stays documented in the README.
