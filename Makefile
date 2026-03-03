.PHONY: clean lint format lint-format test

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
	rm -rf .mypy_cache .pytest_cache .uv_cache .coverage .ruff_cache

lint:
	uvx ruff check . --fix

format:
	uvx ruff format .

lint-format:
	uvx ruff check . --fix
	uvx ruff format .

test:
	uv run pytest
