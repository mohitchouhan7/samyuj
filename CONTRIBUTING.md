# Contributing to Samyuj

## Development Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"
```

## Running the Test Suite

Every change should pass the unit and architectural test suite:

```bash
PYTHONPATH=src pytest tests -q
python3 -m compileall -q src tests
```

## Code Organization

* Keep domain logic inside the appropriate bounded package (`protocol/`, `session/`, `streaming/`, `files/`, `input_hid/`, `services/`, `ui/`, `system/`).
* Prefer behavioral unit and integration tests using in-memory transports and fake streams over source-text inspection.
