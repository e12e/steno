.PHONY: test doctest lint typecheck check test-e2e rerun-big

doctest:
	python3 -m doctest -v mote_core.py transcribe.py

test:
	python3 tests/test_cli_and_pipeline.py -v

lint:
	uvx ruff check mote_core.py transcribe.py mote_app.py mote_gui.py debug_synthetic_test.py tests/

typecheck:
	uvx mypy mote_core.py transcribe.py tests/test_cli_and_pipeline.py

check:
	./scripts/run_checks.sh

test-e2e:
	python3 debug_synthetic_test.py --step run

rerun-big:
	./scripts/rerun_big_notes.sh
