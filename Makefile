.PHONY: setup check test lint typecheck format clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"

check: lint typecheck test

test:
	$(PYTHON) -m pytest tests/ -v || test $$? -eq 5

lint:
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m ruff format --check src/ tests/

typecheck:
	$(PYTHON) -m mypy src/

format:
	$(PYTHON) -m ruff format src/ tests/
	$(PYTHON) -m ruff check --fix src/ tests/

clean:
	rm -rf $(VENV) *.egg-info src/*.egg-info .mypy_cache .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
