PYTHON ?= python3

.PHONY: serve test test-integration coverage-demo

serve:
	$(PYTHON) -m geozigzag

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py"

test-integration:
	$(PYTHON) -m unittest discover -s tests/integration -p "test_*.py"

coverage-demo:
	$(PYTHON) -m geozigzag.coverage_cli --config configs/robot_coverage_demo.json
