import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_normalization import normalize_cv_extraction, canonicalize_skill_name

class EnhancedCvSkillExtractionTests(unittest.TestCase):
    def test_acceptance_test_multi_section_skill_extraction_and_deduplication(self):
        """Acceptance Test: Collect skills from Skills, Experience, Projects, and Certifications,
        deduplicate aliases, infer behavioral evidence, and avoid hallucinations."""
        cv_text = """
        Candidate Name: Alex Mercer
        Email: alex.mercer@example.com

        Professional Summary:
        AI & ML Engineer focused on deep learning, computer vision, and backend integrations.

        Skills:
        Python, PyTorch, Docker, GCP

        Professional Experience:
        AI Research Engineer | Tech Corp | 2022-01 - Present
        Led five annotators and deployed PyTorch models using Docker on GCP.

        Projects:
        1. Explainable Vision Pipeline
           Built a Django application using TensorFlow, MobileNet and transfer learning. Applied SHAP and Grad-CAM.

        Certifications:
        Server-side Development: NodeJS, Express, MongoDB, React.
        Issuer: Coursera
        """

        raw_model_output = {
            "personal_info": {"full_name": "Alex Mercer", "email": "alex.mercer@example.com"},
            "professional_summary": "AI & ML Engineer focused on deep learning, computer vision, and backend integrations.",
            "technical_skills": ["Python", "PyTorch", "Docker", "GCP"],
            "experience": [
                {
                    "company": "Tech Corp",
                    "role": "AI Research Engineer",
                    "start_date": "2022-01",
                    "is_current": True,
                    "description": "Led five annotators and deployed PyTorch models using Docker on GCP.",
                    "skills_used": ["PyTorch", "Docker", "GCP"]
                }
            ],
            "projects": [
                {
                    "name": "Explainable Vision Pipeline",
                    "description": "Built a Django application using TensorFlow, MobileNet and transfer learning. Applied SHAP and Grad-CAM.",
                    "technologies": ["Django", "TensorFlow", "MobileNet", "Transfer Learning", "SHAP", "Grad-CAM"]
                }
            ],
            "certifications": [
                {
                    "name": "Server-side Development: NodeJS, Express, MongoDB, React",
                    "issuer": "Coursera",
                    "relevant_skills": ["NodeJS", "Express", "MongoDB", "React"]
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)

        tech_skills = [t["skill"] for t in normalized["technical_skills"]]
        soft_skills = [s["skill"] for s in normalized["soft_skills"]]
        prog_langs = normalized["programming_languages"]

        all_tech_and_prog = set(tech_skills + prog_langs)

        # Minimum expected technical skills & canonical representations
        expected_tech_skills = {
            "Python", "PyTorch", "Docker", "GCP", "Django", "TensorFlow",
            "MobileNet", "Transfer Learning", "SHAP", "Grad-CAM",
            "Node.js", "Express", "MongoDB", "React"
        }

        for expected in expected_tech_skills:
            self.assertIn(expected, all_tech_and_prog, f"Expected skill '{expected}' was not found in extracted skills.")

        # Behavioral evidence for soft skills
        self.assertIn("Leadership", soft_skills, "Expected 'Leadership' from 'Led five annotators' evidence.")
        self.assertIn("Team Management", soft_skills, "Expected 'Team Management' from 'Led five annotators' evidence.")

        # Deduplication checks: PyTorch, Docker, GCP must appear ONLY ONCE in tech_skills
        for skill in ["PyTorch", "Docker", "GCP", "Node.js"]:
            self.assertEqual(1, tech_skills.count(skill), f"Skill '{skill}' should not be duplicated in technical_skills.")

        # Anti-hallucination checks: Unmentioned tools must NOT be present
        for unmentioned in ["Kubernetes", "Azure", "JavaScript", "Next.js"]:
            self.assertNotIn(unmentioned, all_tech_and_prog, f"Unmentioned technology '{unmentioned}' was hallucinated.")

        # Certification provider ('Coursera') must NOT be extracted as a skill
        self.assertNotIn("Coursera", all_tech_and_prog, "Certification provider 'Coursera' was incorrectly extracted as a skill.")

    def test_canonicalization_of_skill_aliases(self):
        """Verify equivalent skill names normalize to canonical forms."""
        self.assertEqual("React", canonicalize_skill_name("ReactJS"))
        self.assertEqual("React", canonicalize_skill_name("React.js"))
        self.assertEqual("Node.js", canonicalize_skill_name("NodeJS"))
        self.assertEqual("MySQL", canonicalize_skill_name("Mysql"))
        self.assertEqual("PostgreSQL", canonicalize_skill_name("Postgres"))
        self.assertEqual("Scikit-learn", canonicalize_skill_name("Scikit Learn"))
        self.assertEqual("REST APIs", canonicalize_skill_name("REST API"))
        self.assertEqual("CI/CD", canonicalize_skill_name("CI/CD pipeline"))
        self.assertEqual("Explainable AI", canonicalize_skill_name("XAI"))

if __name__ == "__main__":
    unittest.main()
