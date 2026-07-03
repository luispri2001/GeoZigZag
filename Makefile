PYTHON ?= python3
EVALUATION_OUT ?= outputs/evaluation

.PHONY: test evaluate paper reproduce

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py"

evaluate:
	$(PYTHON) -m geozigzag.evaluate --config configs/evaluation.yaml --out $(EVALUATION_OUT)

paper: evaluate
	mkdir -p paper/build
	latexmk -pdf -interaction=nonstopmode -halt-on-error -output-directory=paper/build paper/main.tex

reproduce: test evaluate paper
	@echo "Reproduction complete: $(EVALUATION_OUT) and paper/build/main.pdf"
