.PHONY: test doctest lint typecheck check test-e2e build hooks clean

doctest:
	PYTHONPATH=src python3 -m doctest -v src/mote/core.py src/mote/cli.py

test:
	PYTHONPATH=src python3 tests/test_cli_and_pipeline.py -v

lint:
	uvx ruff check src/ scripts/ tests/ transcribe.py steno.py run_verification_and_data.py

typecheck:
	uvx mypy --ignore-missing-imports src/mote/core.py src/mote/cli.py src/mote/gui.py src/mote/template_loader.py scripts/build_single_file.py transcribe.py steno.py tests/test_cli_and_pipeline.py

check:
	./scripts/run_checks.sh

build:
	python3 scripts/build_single_file.py
	./scripts/test_standalone.sh

test-e2e:
	python3 tests/e2e_benchmark.py --action llm-only

hooks:
	git config core.hooksPath .githooks

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache .mypy_cache __pycache__ src/mote/__pycache__ tests/__pycache__
