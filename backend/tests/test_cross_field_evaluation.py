import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.enterprise_job_evaluation_service import SCORING_RULES_VERSION, deterministic_evaluation, evaluate


class CrossFieldEvaluationTests(unittest.TestCase):
    def test_hr_candidate_uses_role_skills_without_github_penalty(self):
        job = {
            "title": "HR Manager",
            "skills": "Talent Acquisition, Employee Relations, Onboarding, Leadership",
            "description": "Lead recruitment, onboarding, employee relations, and HR operations.",
            "experience": 3,
        }
        candidate = {
            "technical_skills": ["Talent Acquisition", "Employee Relations", "Onboarding"],
            "soft_skills": ["Leadership", "Communication"],
            "experience": [{"duration_months": 48, "position": "HR Executive"}],
            "projects": [{"name": "Onboarding Program", "description": "Redesigned employee onboarding"}],
            "education": [{"qualification": "BSc Human Resource Management"}],
            "certifications": [],
            "human_languages": [{"language": "English"}],
            "personal_info": {"headline": "Human Resources Manager"},
        }

        result = deterministic_evaluation(job, candidate)

        self.assertEqual(30, result["score_breakdown"]["technical_skills"]["score"])
        self.assertEqual(10, result["score_breakdown"]["github_evidence"]["score"])
        self.assertEqual([], result["verification_gaps"])
        self.assertIn("not applicable", result["score_breakdown"]["github_evidence"]["reason"].lower())

    def test_software_role_still_uses_github_as_evidence(self):
        result = deterministic_evaluation(
            {"title": "Python Developer", "skills": "Python", "description": "Build software", "experience": 0},
            {"technical_skills": ["Python"], "personal_info": {}, "experience": []},
        )
        self.assertEqual(0, result["score_breakdown"]["github_evidence"]["score"])
        self.assertTrue(result["verification_gaps"])


class StandardizedEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generative_output_cannot_change_standardized_marks(self):
        job = {
            "title": "Sales Manager",
            "skills": "Negotiation, CRM, Leadership",
            "description": "Lead sales teams using CRM and negotiation skills.",
            "experience": 2,
        }
        candidate = {
            "technical_skills": ["CRM", "Negotiation"],
            "soft_skills": ["Leadership"],
            "experience": [{"duration_months": 24}],
            "personal_info": {"headline": "Sales Manager"},
        }
        model_calls = 0

        async def unstable_model(_prompt):
            nonlocal model_calls
            model_calls += 1
            return {"candidate_score": 1 if model_calls == 1 else 99}

        first, first_provider = await evaluate(job, candidate, unstable_model)
        second, second_provider = await evaluate(job, candidate, unstable_model)

        self.assertEqual(0, model_calls)
        self.assertEqual(first, second)
        self.assertEqual(first["candidate_score"], sum(
            item["score"] for item in first["score_breakdown"].values()
        ))
        self.assertEqual(SCORING_RULES_VERSION, first_provider)
        self.assertEqual(first_provider, second_provider)


if __name__ == "__main__":
    unittest.main()
