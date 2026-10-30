.PHONY: test doctest lint typecheck check test-e2e rerun-big build clean

doctest:
	python3 -m doctest -v src/mote/core.py src/mote/cli.py

test:
	python3 tests/test_cli_and_pipeline.py -v

lint:
	uvx ruff check src/ scripts/ tests/ debug_synthetic_test.py

typecheck:
	uvx mypy src/ tests/

check:
	./scripts/run_checks.sh

build:
	python3 scripts/build_single_file.py
	./scripts/test_standalone.sh

test-e2e:
	python3 debug_synthetic_test.py --step run

rerun-big:
	./scripts/rerun_big_notes.sh

clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache .mypy_cache
