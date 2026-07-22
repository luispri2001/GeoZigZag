from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from .config import load_config
from .demo import create_synthetic_demo
from .pipeline import generate_map

app = typer.Typer(
    name="public-map-generator",
    help="Genera mapas previos de navegación utilizando únicamente datos públicos.",
    no_args_is_help=True,
)
console = Console()


@app.command("generate")
def generate(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Ejecuta el pipeline definido en un YAML."""
    parsed = load_config(config)
    output = generate_map(parsed)
    console.print(Panel.fit(f"Salida: {output}", title="Proceso completado", border_style="green"))


@app.command("validate-config")
def validate_config(
    config: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Valida el YAML sin descargar datos."""
    parsed = load_config(config)
    console.print("[green]Configuración válida.[/green]")
    console.print(parsed.model_dump_json(indent=2))


@app.command("synthetic-demo")
def synthetic_demo(
    output: Path = typer.Option(Path("demo_output"), "--output", "-o"),
) -> None:
    """Genera un ejemplo sintético para verificar la instalación sin Internet."""
    root = create_synthetic_demo(output, overwrite=True)
    console.print(f"[green]Demo creada en {root}[/green]")


if __name__ == "__main__":
    app()
