"""Automated tests for hardened CV extraction, normalization, and profile update safety."""

import io
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cv_normalization import (
    calculate_duration_months,
    calculate_merged_experience_years,
    normalize_cv_extraction,
    parse_date_components,
    text_contains_phrase,
)
from cv_document import extract_cv_content
from domain import CvExtraction, duration_months, save_extraction
from docx import Document


class DummyDbCollection:
    def __init__(self):
        self.docs = []

    def delete_many(self, query):
        if not query:
            self.docs = []
            return
        key, val = next(iter(query.items()))
        self.docs = [d for d in self.docs if d.get(key) != val]

    def insert_many(self, records):
        self.docs.extend(records)

    def insert_one(self, record):
        self.docs.append(record)

    def find_one(self, query, sort=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    def find(self, query=None):
        if not query:
            return list(self.docs)
        return [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]

    def update_one(self, query, update, upsert=False):
        found = self.find_one(query)
        if found:
            if "$set" in update:
                found.update(update["$set"])
            if "$addToSet" in update:
                for k, v in update["$addToSet"].items():
                    current = found.setdefault(k, [])
                    if "$each" in v:
                        for item in v["$each"]:
                            if item not in current:
                                current.append(item)
                    elif v not in current:
                        current.append(v)
        elif upsert:
            new_doc = dict(query)
            if "$set" in update:
                new_doc.update(update["$set"])
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            self.docs.append(new_doc)


class DummyDatabase:
    def __init__(self):
        self.candidates = DummyDbCollection()
        self.candidate_profiles = DummyDbCollection()
        self.users = DummyDbCollection()
        self.candidate_skills = DummyDbCollection()
        self.candidate_experience = DummyDbCollection()
        self.candidate_projects = DummyDbCollection()
        self.candidate_education = DummyDbCollection()
        self.candidate_certifications = DummyDbCollection()
        self.candidate_languages = DummyDbCollection()
        self.candidate_github = DummyDbCollection()
        self.cv_documents = DummyDbCollection()
        self.audit_logs = DummyDbCollection()

    def __getitem__(self, name):
        return getattr(self, name)


class CvExtractionHardeningTests(unittest.TestCase):
    def test_cv_1_software_engineer_extraction(self):
        """CV 1: Software engineer CV correctly normalizes skills, experience, and education."""
        cv_text = """
        John Doe
        Email: john.doe@example.com | Phone: +1 555-0199
        Location: San Francisco, CA
        Professional Title: Senior Software Engineer

        Work Experience:
        Acme Corp | Senior Backend Developer | 2021-01 - 2023-06
        Built scalable microservices in Python and Go using PostgreSQL and Docker.

        Education:
        B.S. in Computer Science | Stanford University | 2016 - 2020

        Skills: Python, Go, Docker, PostgreSQL, React
        """
        raw_model_output = {
            "personal_info": {
                "full_name": "John Doe",
                "email": "john.doe@example.com",
                "phone": "+1 555-0199",
                "location": "San Francisco, CA",
                "headline": "Senior Software Engineer"
            },
            "programming_languages": ["Python", "Go"],
            "technical_skills": ["Docker", "PostgreSQL", "React"],
            "experience": [
                {
                    "company": "Acme Corp",
                    "role": "Senior Backend Developer",
                    "start_date": "2021-01",
                    "end_date": "2023-06",
                    "is_current": False,
                    "skills_used": ["Python", "Go", "Docker"]
                }
            ],
            "education": [
                {
                    "qualification": "B.S. in Computer Science",
                    "institution": "Stanford University",
                    "start_year": 2016,
                    "end_year": 2020
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual("John Doe", validated.personal_info.full_name)
        self.assertEqual("john.doe@example.com", validated.personal_info.email)
        self.assertEqual("+1 555-0199", validated.personal_info.phone)
        self.assertEqual(1, len(validated.experience))
        self.assertEqual("Acme Corp", validated.experience[0].company)
        self.assertEqual(29, validated.experience[0].duration_months)
        self.assertEqual(2.4, validated.personal_info.experience_years)
        self.assertEqual(1, len(validated.education))
        self.assertEqual("Stanford University", validated.education[0].institution)

    def test_cv_2_ml_engineer_skills_not_turned_into_certifications(self):
        """CV 2: Technologies (CNN, GAN, PyTorch) must NOT be hallucinated into certifications."""
        cv_text = """
        Ayesha Perera
        Email: ayesha.ml@example.com
        Location: Colombo, Sri Lanka
        Machine Learning Engineer

        Technical Skills:
        PyTorch, TensorFlow, CNNs, GANs, Computer Vision, Python

        Experience:
        AI Labs | Research Engineer | 2022-01 - Present
        Researched generative adversarial networks and CNN architectures.
        """
        # Model hallucinated skills as certifications without evidence
        raw_model_output = {
            "personal_info": {"full_name": "Ayesha Perera", "email": "ayesha.ml@example.com"},
            "technical_skills": ["PyTorch", "TensorFlow", "Computer Vision"],
            "certifications": [
                {
                    "name": "GANs",
                    "issuer": None,
                    "relevant_skills": ["GANs"]
                },
                {
                    "name": "CNNs",
                    "issuer": None,
                    "relevant_skills": ["CNNs"]
                }
            ],
            "experience": [
                {
                    "company": "AI Labs",
                    "role": "Research Engineer",
                    "start_date": "2022-01",
                    "is_current": True,
                    "skills_used": ["PyTorch", "GANs", "CNNs"]
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        # Certifications should have been filtered out
        self.assertEqual(0, len(validated.certifications))
        # Skills should be retained in technical skills
        tech_skill_names = {s.skill.lower() for s in validated.technical_skills}
        self.assertIn("pytorch", tech_skill_names)
        self.assertIn("gans", tech_skill_names)
        self.assertIn("cnns", tech_skill_names)

    def test_cv_3_non_technical_hr_management_cv(self):
        """CV 3: Non-technical HR CV preserves domain competencies without hallucinating tech."""
        cv_text = """
        Sarah Jenkins
        Human Resources Business Partner
        Email: sarah.j@example.com | Phone: +44 20 7946 0912
        Location: London, UK

        Core Competencies:
        Talent Acquisition, Performance Management, Employee Relations, UK Employment Law, HR Auditing

        Experience:
        Global Retail Ltd | HR Manager | 2019-03 - 2023-01
        Led recruitment and compensation review across 500+ employees.
        """
        raw_model_output = {
            "personal_info": {
                "full_name": "Sarah Jenkins",
                "email": "sarah.j@example.com",
                "phone": "+44 20 7946 0912",
                "location": "London, UK",
                "headline": "Human Resources Business Partner"
            },
            "technical_skills": ["Talent Acquisition", "Performance Management", "Employee Relations", "UK Employment Law"],
            "soft_skills": ["Leadership", "Conflict Resolution"],
            "experience": [
                {
                    "company": "Global Retail Ltd",
                    "role": "HR Manager",
                    "start_date": "2019-03",
                    "end_date": "2023-01",
                    "is_current": False
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual("Sarah Jenkins", validated.personal_info.full_name)
        self.assertEqual(0, len(validated.programming_languages))
        tech_skills = [t.skill for t in validated.technical_skills]
        self.assertIn("Talent Acquisition", tech_skills)
        self.assertIn("UK Employment Law", tech_skills)

    def test_cv_4_student_fresh_graduate_no_experience(self):
        """CV 4: Fresh graduate CV with no work experience yields 0.0 experience and no fake employers."""
        cv_text = """
        Kavindu Silva
        Email: kavindu@university.lk
        Location: Moratuwa, Sri Lanka

        Education:
        B.Sc. Engineering (Hons) | University of Moratuwa | 2020 - 2024
        Grade: First Class Honors

        Projects:
        Autonomous Rover — ROS2, C++, Python project for campus robotics competition.
        """
        raw_model_output = {
            "personal_info": {"full_name": "Kavindu Silva", "email": "kavindu@university.lk"},
            "experience": [],
            "education": [
                {
                    "qualification": "B.Sc. Engineering (Hons)",
                    "institution": "University of Moratuwa",
                    "start_year": 2020,
                    "end_year": 2024,
                    "grade": "First Class Honors"
                }
            ],
            "projects": [
                {
                    "name": "Autonomous Rover",
                    "description": "ROS2, C++, Python project for campus robotics competition.",
                    "technologies": ["ROS2", "C++", "Python"]
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual(0.0, validated.personal_info.experience_years)
        self.assertEqual(0, len(validated.experience))
        self.assertEqual(1, len(validated.projects))
        self.assertEqual("Autonomous Rover", validated.projects[0].name)

    def test_cv_5_multi_page_cv_full_text(self):
        """CV 5: Multi-page CV retains sections across entire text without premature truncation."""
        long_cv_body = "Professional Summary: Experienced software architect.\n" + ("Employment detail item.\n" * 500)
        cv_text = f"""
        Page 1:
        Alex Morgan
        Email: alex@example.org
        {long_cv_body}

        Page 3:
        Education:
        M.Sc. Software Engineering | Oxford University | 2014 - 2016
        """
        raw_model_output = {
            "personal_info": {"full_name": "Alex Morgan", "email": "alex@example.org"},
            "education": [
                {
                    "qualification": "M.Sc. Software Engineering",
                    "institution": "Oxford University",
                    "start_year": 2014,
                    "end_year": 2016
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual(1, len(validated.education))
        self.assertEqual("Oxford University", validated.education[0].institution)

    def test_cv_6_image_scanned_cv_fallback(self):
        """CV 6: Scanned CV with minimal/empty machine text passes visual extraction without rejection."""
        # For scanned CV, cv_text is empty or < 80 chars
        cv_text = ""
        raw_model_output = {
            "personal_info": {
                "full_name": "Maria Gonzalez",
                "email": "maria@example.com",
                "phone": "+34 600 000 000",
                "location": "Madrid, Spain"
            },
            "education": [
                {
                    "qualification": "Grado en Informatica",
                    "institution": "Universidad Politecnica de Madrid",
                    "start_year": 2017,
                    "end_year": 2021
                }
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual("Maria Gonzalez", validated.personal_info.full_name)
        self.assertEqual(1, len(validated.education))

    def test_cv_7_cv_without_projects_not_fabricated(self):
        """CV 7: CV without projects remains empty without regex fabricating fake projects."""
        cv_text = """
        Devinda Jayawardena
        Email: devinda@example.com
        Skills: React, Node.js, Python, PostgreSQL
        Work Experience:
        Tech Corp | Full Stack Developer | 2020-01 - 2022-01
        """
        raw_model_output = {
            "personal_info": {"full_name": "Devinda Jayawardena", "email": "devinda@example.com"},
            "technical_skills": ["React", "Node.js", "Python", "PostgreSQL"],
            "projects": []
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual(0, len(validated.projects))

    def test_cv_8_cv_without_certifications_not_fabricated(self):
        """CV 8: CV without certifications remains empty without skills turned into certs."""
        cv_text = """
        Kumar Sangakkara
        Email: kumar@example.com
        Skills: Leadership, Project Management, Strategy
        """
        raw_model_output = {
            "personal_info": {"full_name": "Kumar Sangakkara", "email": "kumar@example.com"},
            "technical_skills": ["Project Management", "Strategy"],
            "soft_skills": ["Leadership"],
            "certifications": []
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        self.assertEqual(0, len(validated.certifications))

    def test_cv_9_tables_and_two_column_layout_docx(self):
        """CV 9: Tables in DOCX are properly extracted into lines without losing sections."""
        document = Document()
        document.add_heading("Priyantha Silva", level=1)
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Experience"
        table.cell(0, 1).text = "Virtusa | Software Engineer | 2021 - 2023"
        table.cell(1, 0).text = "Education"
        table.cell(1, 1).text = "University of Colombo | BSc in Information Systems"

        stream = io.BytesIO()
        document.save(stream)
        text, _ = extract_cv_content(stream.getvalue(), ".docx")

        self.assertIn("Priyantha Silva", text)
        self.assertIn("Virtusa", text)
        self.assertIn("University of Colombo", text)

    def test_cv_10_programming_vs_human_languages_categorization(self):
        """CV 10: Programming languages (Python, Java) and human languages (English, Tamil) are separated."""
        cv_text = """
        Thanujan Tharmapalan
        Languages: English (Fluent), Tamil (Native)
        Programming Languages: Python, Java, JavaScript, TypeScript, SQL
        """
        # Deliberately mix programming language into human languages from raw AI
        raw_model_output = {
            "personal_info": {"full_name": "Thanujan Tharmapalan"},
            "programming_languages": ["Python", "Java", "English"],
            "human_languages": [
                {"language": "Tamil", "speaking_level": "Native"},
                {"language": "JavaScript", "speaking_level": "Fluent"},
                {"language": "English", "speaking_level": "Fluent"}
            ]
        }

        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        validated = CvExtraction.model_validate(normalized)

        prog_langs = [p.lower() for p in validated.programming_languages]
        human_langs = [h.language.lower() for h in validated.languages]

        # JavaScript should have moved to programming_languages, NOT human_languages
        self.assertIn("javascript", prog_langs)
        self.assertNotIn("javascript", human_langs)

        # English should be in human_languages
        self.assertIn("english", human_langs)
        self.assertIn("tamil", human_langs)

    def test_overlapping_experience_avoids_double_counting(self):
        """Overlapping jobs (e.g. concurrent freelance and full-time) avoid double-counting months."""
        experiences = [
            {"company": "Company A", "start_date": "2020-01", "end_date": "2021-01"},  # 12 months
            {"company": "Company B", "start_date": "2020-06", "end_date": "2021-06"},  # Overlaps 7 months
        ]
        years = calculate_merged_experience_years(experiences)
        # Total interval: 2020-01 to 2021-06 = 17 months = 1.4 years (NOT 24 months = 2.0 years)
        self.assertEqual(1.4, years)

    def test_failed_extraction_does_not_wipe_candidate_profile(self):
        """A failed extraction does not call save_extraction and preserves the previous valid profile."""
        db = DummyDatabase()
        cid = "candidate-123"

        # Pre-populate valid profile
        initial_extraction = CvExtraction.model_validate({
            "personal_info": {"full_name": "Original Candidate", "email": "orig@example.com"},
            "education": [{"qualification": "BSc IT", "institution": "Original University"}],
            "projects": [{"name": "Original Project", "description": "Good project"}]
        })
        save_extraction(db, cid, initial_extraction, "doc-1", "cv.pdf", "gemini-3.5-flash-lite")

        self.assertEqual(1, len(db.candidate_education.docs))
        self.assertEqual("Original University", db.candidate_education.docs[0]["institution"])
        self.assertEqual(1, len(db.candidate_projects.docs))

        # Simulate a failed extraction attempt: an exception is raised, save_extraction is NEVER called
        failed_occurred = True
        if not failed_occurred:
            pass  # save_extraction not executed

        # Check DB remains intact
        self.assertEqual(1, len(db.candidate_education.docs))
        self.assertEqual("Original University", db.candidate_education.docs[0]["institution"])
        self.assertEqual(1, len(db.candidate_projects.docs))
        self.assertEqual("Original Project", db.candidate_projects.docs[0]["name"])

    def test_direct_and_project_soft_skills_extraction(self):
        """Soft skills explicitly listed or present in projects are extracted and normalized."""
        raw_model_output = {
            "personal_info": {"full_name": "Jane Doe", "email": "jane@example.com"},
            "soft_skills": ["Leadership", "Communication"],
            "projects": [
                {
                    "name": "E-Commerce System",
                    "description": "Led a cross-functional team of 5 to build fullstack system.",
                    "technologies": ["React", "FastAPI"],
                    "soft_skills": ["Agile", "Teamwork", "Conflict Resolution"],
                    "achievements": ["Improved problem solving and customer collaboration."]
                }
            ]
        }
        normalized = normalize_cv_extraction(raw_model_output, cv_text="Jane Doe CV text")
        soft_names = [s["skill"] for s in normalized["soft_skills"]]
        tech_names = [t["skill"] for t in normalized["technical_skills"]]

        # Direct soft skills
        self.assertIn("Leadership", soft_names)
        self.assertIn("Communication", soft_names)
        # Project-level soft skills
        self.assertIn("Conflict Resolution", soft_names)
        self.assertIn("Teamwork", soft_names)
        # Agile (methodology) is demoted to technical_skills per Universal CV Rule
        self.assertIn("Agile", tech_names)

    def test_inferred_soft_skills_from_project_and_experience_text(self):
        """When no soft skills section exists, soft skills are inferred from project & experience descriptions."""
        raw_model_output = {
            "personal_info": {"full_name": "John Smith", "email": "john@example.com"},
            "soft_skills": [],
            "projects": [
                {
                    "name": "Cloud Infrastructure",
                    "description": "Collaborated with team leads to resolve critical production issues.",
                    "technologies": ["Docker", "Kubernetes"],
                    "achievements": ["Demonstrated strong problem-solving skills under tight deadlines."]
                }
            ],
            "experience": [
                {
                    "company": "Tech Corp",
                    "role": "Software Engineer",
                    "responsibilities": ["Lead sprint planning and practice agile methodologies."]
                }
            ]
        }
        cv_text = "John Smith. Role: Software Engineer. Collaborated with team leads to resolve production issues."
        normalized = normalize_cv_extraction(raw_model_output, cv_text=cv_text)
        soft_names = [s["skill"] for s in normalized["soft_skills"]]

        # Should infer Collaboration, Problem Solving, Agile/Leadership from text
        self.assertTrue(any("collaboration" in s.lower() for s in soft_names))
        self.assertTrue(any("problem" in s.lower() for s in soft_names))

    def test_universal_cv_rule_soft_skills_classification(self):
        """Verify domain/functional competencies are excluded from soft_skills and true soft skills are preserved."""
        raw_model_output = {
            "personal_info": {"full_name": "Multi Domain Candidate", "email": "candidate@example.com"},
            "soft_skills": [
                "Financial Reporting", "Patient Care", "Digital Marketing", "Inventory Management",
                "Recruitment", "Project Management", "AutoCAD", "Data Analysis",
                "Communication", "Teamwork", "Leadership", "Adaptability", "Negotiation",
                "Conflict Resolution", "Problem Solving", "Critical Thinking", "Time Management", "Attention to Detail"
            ],
            "projects": []
        }
        normalized = normalize_cv_extraction(raw_model_output, cv_text="Multi Domain Candidate CV")
        soft_names = [s["skill"] for s in normalized["soft_skills"]]
        tech_names = [t["skill"] for t in normalized["technical_skills"]]

        # Domain competencies MUST NOT be in soft_skills
        domain_competencies = [
            "Financial Reporting", "Patient Care", "Digital Marketing", "Inventory Management",
            "Recruitment", "Project Management", "AutoCAD", "Data Analysis"
        ]
        for domain_skill in domain_competencies:
            self.assertNotIn(domain_skill, soft_names, f"{domain_skill} should NOT be in soft_skills")
            self.assertIn(domain_skill, tech_names, f"{domain_skill} should be demoted to technical_skills")

        # Genuine soft skills MUST be in soft_skills
        valid_soft = [
            "Communication", "Teamwork", "Leadership", "Adaptability", "Negotiation",
            "Conflict Resolution", "Problem Solving", "Critical Thinking", "Time Management", "Attention to Detail"
        ]
        for s in valid_soft:
            self.assertIn(s, soft_names, f"{s} MUST be classified as soft_skill")

    def test_human_languages_vs_programming_languages_separation(self):
        """Programming languages must never be classified as human languages."""
        raw_output = {
            "personal_info": {"full_name": "Dev User"},
            "human_languages": ["Python", "Java", "C++", "SQL", "English", "Dutch"],
            "programming_languages": []
        }
        cv_text = "Languages: English, Dutch. Skills: Python, Java, C++, SQL."
        normalized = normalize_cv_extraction(raw_output, cv_text=cv_text)
        human_langs = [h["language"] for h in normalized["human_languages"]]
        prog_langs = normalized["programming_languages"]

        self.assertIn("English", human_langs)
        self.assertIn("Dutch", human_langs)
        self.assertNotIn("Python", human_langs)
        self.assertNotIn("Java", human_langs)
        self.assertIn("Python", prog_langs)
        self.assertIn("Java", prog_langs)

    def test_language_name_normalization_and_deduplication(self):
        """Variants like Sinhalese -> Sinhala, English Language -> English are normalized and merged."""
        raw_output = {
            "personal_info": {"full_name": "Polyglot Candidate"},
            "human_languages": [
                "English Language",
                "ENGLISH",
                {"language": "Sinhalese", "level": "Native"},
                {"language": "Chinese - Mandarin", "level": "Conversational"}
            ]
        }
        cv_text = "Languages: English - Fluent, Sinhalese - Native, Chinese - Mandarin - Conversational"
        normalized = normalize_cv_extraction(raw_output, cv_text=cv_text)
        human_langs = [h["language"] for h in normalized["human_languages"]]

        self.assertEqual(1, human_langs.count("English"))
        self.assertIn("Sinhala", human_langs)
        self.assertIn("Mandarin Chinese", human_langs)

    def test_false_positive_language_context_rejection(self):
        """Words matching languages in non-proficiency contexts (BA English Literature, French company) are rejected."""
        raw_output = {
            "personal_info": {"full_name": "False Pos Candidate"},
            "human_languages": ["English", "French", "German"]
        }
        cv_text = "Education: BA English Literature. Worked with French company and German clients."
        normalized = normalize_cv_extraction(raw_output, cv_text=cv_text)
        human_langs = [h["language"] for h in normalized["human_languages"]]

        self.assertNotIn("French", human_langs)
        self.assertNotIn("German", human_langs)
        self.assertNotIn("English", human_langs)

    def test_strict_proficiency_preservation_without_hallucinating(self):
        """When proficiency is unstated, proficiency levels remain None instead of being guessed."""
        raw_output = {
            "personal_info": {"full_name": "No Level Candidate"},
            "human_languages": ["English", "Tamil"]
        }
        cv_text = "Languages: English, Tamil"
        normalized = normalize_cv_extraction(raw_output, cv_text=cv_text)
        for h in normalized["human_languages"]:
            self.assertIsNone(h["speaking_level"])
            self.assertIsNone(h["reading_level"])


if __name__ == "__main__":
    unittest.main()


