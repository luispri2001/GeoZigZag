# Compile the manuscript

## Overleaf

Upload the **contents** of this `paper/` directory, preserving these folders:

```text
main.tex
references.bib
figures/
generated/
```

Select `main.tex` as the main document and `pdfLaTeX` as the compiler. Do not
upload only `main.tex`: the PNG figures and generated table fragments are part
of the manuscript.

The source also supports keeping `paper/` inside the complete repository and
selecting `paper/main.tex` as Overleaf's main document.

## Local standalone compilation

From this directory:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

From the repository root, the reproducible build remains:

```bash
make reproduce
```
