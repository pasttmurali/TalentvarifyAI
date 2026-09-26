import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain import enriched_technical_skills, reset_candidate_profile


class ProfileEnrichmentTests(unittest.TestCase):
    def test_project_and_certification_skills_are_merged(self):
        data = {
            "technical_skills": [
                {"skill": "Python", "category": "Programming", "source": "cv",
                 "evidence": ["Skills section"], "confidence": 0.95}
            ],
            "projects": [
                {"name": "Vision API", "technologies": ["Python", "PyTorch", "Docker"]}
            ],
            "certifications": [
                {"name": "Deep Learning Specialization", "relevant_skills": ["PyTorch", "CNNs"]}
            ],
        }
        result = enriched_technical_skills(data)
        by_name = {item["skill"]: item for item in result}

        self.assertEqual({"Python", "PyTorch", "Docker", "CNNs"}, set(by_name))
        self.assertIn("Project: Vision API", by_name["PyTorch"]["evidence"])
        self.assertIn("Certification: Deep Learning Specialization", by_name["CNNs"]["evidence"])
        self.assertEqual(1, sum(item["skill"] == "Python" for item in result))

    def test_empty_or_duplicate_values_are_ignored_case_insensitively(self):
        result = enriched_technical_skills({
            "technical_skills": [],
            "projects": [{"name": "API", "technologies": ["React", " react ", ""]}],
            "certifications": [{"name": "Web", "relevant_skills": [None, "REACT"]}],
        })
        self.assertEqual(1, len(result))
        self.assertEqual("React", result[0]["skill"])
        self.assertEqual(["Project: API", "Certification: Web"], result[0]["evidence"])

    def test_project_soft_skills_are_included_in_candidate_summary(self):
        projects = [{
            "name": "Customer Portal",
            "soft_skills": ["Leadership", "Communication", " Problem Solving "],
            "technologies": ["React"],
        }]
        flattened = []
        for project in projects:
            for skill in project.get("soft_skills") or []:
                cleaned = " ".join(str(skill or "").split())
                if cleaned:
                    flattened.append(cleaned)
        self.assertEqual(["Leadership", "Communication", "Problem Solving"], flattened)

    def test_reset_candidate_profile_only_affects_candidate_scope(self):
        calls = []

        def record(name, *args, **kwargs):
            calls.append((name, args, kwargs))

        db = SimpleNamespace(
            users=SimpleNamespace(delete_many=lambda *a, **k: record("users")),
            candidates=SimpleNamespace(delete_many=lambda *a, **k: record("candidates")),
            candidate_profiles=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_profiles")),
            candidate_skills=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_skills")),
            candidate_experience=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_experience")),
            candidate_education=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_education")),
            candidate_projects=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_projects")),
            candidate_certifications=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_certifications")),
            candidate_languages=SimpleNamespace(delete_many=lambda *a, **k: record("candidate_languages")),
            cv_documents=SimpleNamespace(delete_many=lambda *a, **k: record("cv_documents")),
        )

        reset_candidate_profile(db, "cand-1")

        self.assertIn("candidate_profiles", {name for name, *_ in calls})
        self.assertIn("candidate_skills", {name for name, *_ in calls})
        self.assertNotIn("users", {name for name, *_ in calls})


if __name__ == "__main__":
    unittest.main()
