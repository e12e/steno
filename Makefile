.PHONY: test doctest lint typecheck check test-e2e build clean

doctest:
	python3 -m doctest -v src/mote/core.py src/mote/cli.py

test:
	python3 tests/test_cli_and_pipeline.py -v

lint:
	uvx ruff check src/ scripts/ tests/ transcribe.py steno.py run_verification_and_data.py

typecheck:
	uvx mypy src/mote/core.py src/mote/cli.py transcribe.py tests/test_cli_and_pipeline.py

check:
	./scripts/run_checks.sh

build:
	python3 scripts/build_single_file.py
	./scripts/test_standalone.sh

test-e2e:
	python3 tests/e2e_benchmark.py --action llm-only

clean:
	rm -rf build/ *.egg-info .pytest_cache .ruff_cache .mypy_cache __pycache__ src/mote/__pycache__ tests/__pycache__
