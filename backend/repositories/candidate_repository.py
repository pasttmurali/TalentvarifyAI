"""
MongoDB repository for candidate profiles and associated domain entities.

This repository acts as the Data Access Layer (DAL) for candidate profile documents,
providing clean CRUD (Create, Read, Update, Delete) methods.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pymongo.database import Database
from pymongo import ReturnDocument


def utcnow():
    """Helper function to return current timestamp in UTC timezone format."""
    return datetime.now(timezone.utc)


def _without_mongo_id(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Helper function to strip MongoDB's internal '_id' ObjectId field before
    returning JSON responses to the frontend client.
    """
    if document is None:
        return None
    result = dict(document)
    result.pop("_id", None)
    return result


class CandidateRepository:
    """Repository class encapsulating all MongoDB database calls for candidate records."""

    def __init__(self, db: Database):
        # Store active MongoDB database connection reference
        self.db = db

    def ensure_indexes(self):
        """
        Create database indexes on the 'candidates' collection.
        Indexes significantly speed up searches by user_id, id, or email.
        """
        self.db.candidates.create_index("user_id", unique=True)
        self.db.candidates.create_index("id", unique=True)
        self.db.candidates.create_index("personal_info.email")

    def find_by_user_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Find a candidate profile by their unique user ID string.
        Returns the candidate document or None if not found.
        """
        return self.db.candidates.find_one({"user_id": user_id}) or self.db.candidates.find_one({"id": user_id})

    def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Search for a candidate profile using their email address.
        Applies lowercase normalization for clean matching.
        """
        clean_email = email.strip().lower() if email else ""
        if not clean_email:
            return None
        doc = self.db.candidates.find_one({"personal_info.email": clean_email})
        if not doc:
            user_doc = self.db.users.find_one({"email": clean_email, "role": "candidate"})
            if user_doc:
                return self.find_by_user_id(user_doc["id"])
        return doc

    def save_or_update_profile(self, candidate_id: str, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Save a new candidate profile or update an existing candidate profile.
        Uses upsert=True to create the record if it doesn't exist yet.
        """
        stamp = utcnow()
        existing = self.find_by_user_id(candidate_id)
        
        doc_to_set = {
            **profile_data,
            "id": candidate_id,
            "user_id": candidate_id,
            "updated_at": stamp,
        }
        
        if existing:
            # Merge existing personal info and social links without overwriting valid data
            merged_info = {**(existing.get("personal_info") or {}), **(profile_data.get("personal_info") or {})}
            merged_links = {**(existing.get("social_links") or {}), **(profile_data.get("social_links") or {})}
            doc_to_set["personal_info"] = merged_info
            doc_to_set["social_links"] = merged_links
            
            result = self.db.candidates.find_one_and_update(
                {"id": candidate_id},
                {"$set": doc_to_set},
                return_document=ReturnDocument.AFTER,
                upsert=True
            )
            return _without_mongo_id(result)
        else:
            doc_to_set["created_at"] = stamp
            self.db.candidates.insert_one(doc_to_set)
            return _without_mongo_id(doc_to_set)

    def get_candidate_skills(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve embedded list of technical and soft skills for a candidate."""
        cand = self.find_by_user_id(candidate_id) or {}
        raw_skills = cand.get("skills") or []
        if isinstance(raw_skills, list):
            return [{"skill": s, "normalized_skill": str(s).lower()} if isinstance(s, str) else s for s in raw_skills]
        return []

    def get_candidate_experience(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve embedded list of work experience entries for a candidate."""
        cand = self.find_by_user_id(candidate_id) or {}
        return cand.get("experience") or []

    def get_candidate_education(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve embedded list of education entries for a candidate."""
        cand = self.find_by_user_id(candidate_id) or {}
        return cand.get("education") or []

    def get_candidate_projects(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve embedded list of portfolio projects for a candidate."""
        cand = self.find_by_user_id(candidate_id) or {}
        return cand.get("projects") or []

    def get_candidate_certifications(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve embedded list of professional certifications for a candidate."""
        cand = self.find_by_user_id(candidate_id) or {}
        return cand.get("certifications") or []

    def get_candidate_languages(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve embedded list of languages spoken/written by a candidate."""
        cand = self.find_by_user_id(candidate_id) or {}
        return cand.get("languages") or []
