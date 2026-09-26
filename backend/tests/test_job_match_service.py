import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.job_match_service import JobMatchService, nonnegative_number


class JobMatchServiceTests(unittest.TestCase):
    def test_missing_and_malformed_experience_durations_do_not_crash(self):
        result = JobMatchService.calculate_job_match_score(
            {
                "personal_info": {"experience_years": None},
                "technical_skills": ["Recruitment"],
                "experience": [
                    {"duration_months": None},
                    {"duration_months": "24"},
                    {"duration_months": "not-recorded"},
                    {"duration_months": -6},
                ],
            },
            {"id": "job-1", "title": "HR Executive", "skills": "Recruitment", "experience": 4},
        )

        self.assertEqual(10.0, result["score_breakdown"]["experience"]["score"])
        self.assertIn("2 years", result["score_breakdown"]["experience"]["reason"])

    def test_nonnegative_number_rejects_non_finite_values(self):
        self.assertEqual(0.0, nonnegative_number(None))
        self.assertEqual(0.0, nonnegative_number("invalid"))
        self.assertEqual(0.0, nonnegative_number(float("nan")))
        self.assertEqual(0.0, nonnegative_number(float("inf")))
        self.assertEqual(18.0, nonnegative_number("18"))


if __name__ == "__main__":
    unittest.main()
