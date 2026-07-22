.PHONY: install install-all test lint demo ui clean

install:
	python -m pip install -e .

install-all:
	python -m pip install -e '.[all,dev]'

test:
	pytest -q

lint:
	ruff check src tests

demo:
	pmg synthetic-demo --output demo_output

ui:
	streamlit run app.py

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache outputs
