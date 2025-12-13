import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ams.models import SubmissionContext, StepResult
from ams.scoring import ScoreAggregator


class ScoreAggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = SubmissionContext(
            submission_zip=Path("dummy.zip"),
            extracted_path=Path("extracted"),
            normalized_files=[],
        )
        self.aggregator = ScoreAggregator()

    def test_no_steps_defaults_to_zero(self) -> None:
        report = self.aggregator.aggregate(self.context, [])
        self.assertEqual(report.total_score, 0.0)

    def test_high_ratio_maps_to_full_score(self) -> None:
        steps = [StepResult(name="a", score=1.0), StepResult(name="b", score=1.0)]
        report = self.aggregator.aggregate(self.context, steps)
        self.assertEqual(report.total_score, 1.0)

    def test_mid_ratio_maps_to_partial(self) -> None:
        steps = [StepResult(name="a", score=1.0), StepResult(name="b", score=0.0)]
        report = self.aggregator.aggregate(self.context, steps)
        self.assertEqual(report.total_score, 0.5)

    def test_low_ratio_maps_to_zero(self) -> None:
        steps = [StepResult(name="a", score=0.0), StepResult(name="b", score=0.0)]
        report = self.aggregator.aggregate(self.context, steps)
        self.assertEqual(report.total_score, 0.0)


if __name__ == "__main__":
    unittest.main()
