PYTHON ?= python3
EVALUATION_OUT ?= outputs/evaluation

.PHONY: test evaluate sync-paper-assets paper reproduce

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py"

evaluate:
	$(PYTHON) -m geozigzag.evaluate --config configs/evaluation.yaml --out $(EVALUATION_OUT)

sync-paper-assets: evaluate
	mkdir -p paper/generated paper/figures/generated
	cp $(EVALUATION_OUT)/paper_results.tex paper/generated/paper_results.tex
	cp $(EVALUATION_OUT)/figures/*.png paper/figures/generated/

paper: sync-paper-assets
	mkdir -p paper/build
	latexmk -pdf -interaction=nonstopmode -halt-on-error -output-directory=paper/build paper/main.tex
	cp paper/build/main.pdf paper/main.pdf

reproduce: test evaluate paper
	@echo "Reproduction complete: $(EVALUATION_OUT) and paper/build/main.pdf"
