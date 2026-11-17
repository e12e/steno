.PHONY: test doctest lint typecheck check test-e2e build hooks clean

doctest:
	PYTHONPATH=src python3 -m doctest -v src/mote/constants.py src/mote/context.py src/mote/hardware.py src/mote/render.py src/mote/audio.py src/mote/pipeline.py src/mote/core.py src/mote/cli.py src/mote/gui_bridge.py src/mote/gui.py

test:
	PYTHONPATH=src python3 tests/test_cli_and_pipeline.py -v

lint:
	uvx ruff check src/ scripts/ tests/ transcribe.py steno.py

typecheck:
	uvx mypy --ignore-missing-imports src/mote/ scripts/build_single_file.py transcribe.py steno.py tests/test_cli_and_pipeline.py

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
