import csv
import json
import tempfile
import unittest
from pathlib import Path

from geozigzag.evaluation import run_evaluation


class EvaluationTests(unittest.TestCase):
    def test_evaluation_writes_publication_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary, "evaluation")
            summary = run_evaluation("configs/evaluation.yaml", output)
            required = {
                "summary.json",
                "results.csv",
                "sensitivity.csv",
                "paper_results.tex",
                "paper_coverage_results.tex",
                "figures/comparison_north_loop.png",
                "figures/sensitivity.png",
            }
            self.assertTrue(all((output / name).is_file() for name in required))
            self.assertEqual(len(summary["results"]), 12)
            disk_summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(disk_summary["seed"], 20260703)
            with (output / "results.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(all(float(row["success_rate"]) == 1.0 for row in rows))


if __name__ == "__main__":
    unittest.main()
