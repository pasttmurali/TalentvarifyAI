"""MongoDB repository for GitHub profiles, repositories, and verified evidence."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pymongo.database import Database
from pymongo import ReturnDocument


def utcnow():
    return datetime.now(timezone.utc)


class GithubRepository:
    def __init__(self, db: Database):
        self.db = db

    def ensure_indexes(self):
        self.db.candidate_github.create_index("candidate_id", unique=True)
        self.db.github_evidence_snapshots.create_index([("candidate_id", 1), ("scanned_at", -1)])

    def save_github_snapshot(self, candidate_id: str, snapshot_doc: Dict[str, Any]) -> Dict[str, Any]:
        snapshot_id = snapshot_doc.get("id") or snapshot_doc.get("_id")
        snapshot_doc["candidate_id"] = candidate_id
        snapshot_doc["_id"] = snapshot_id
        snapshot_doc["id"] = snapshot_id
        
        # Save snapshot into historical snapshot log (immutable)
        self.db.github_evidence_snapshots.update_one({"_id": snapshot_id}, {"$set": snapshot_doc}, upsert=True)
        
        # Update current active github profile
        profile_doc = {
            "candidate_id": candidate_id,
            "github_url": snapshot_doc.get("github_url"),
            "username": snapshot_doc.get("username"),
            "latest_snapshot_id": snapshot_id,
            "last_verified_at": snapshot_doc.get("scanned_at"),
            "verification_status": snapshot_doc.get("verification_status", "verified"),
            "profile": snapshot_doc.get("profile", {}),
            "repositories": snapshot_doc.get("repositories", []),
            "detected_skills": snapshot_doc.get("detected_skills", []),
            "skill_evidence": snapshot_doc.get("skill_evidence", []),
            "cv_consistency": snapshot_doc.get("cv_consistency", {}),
            "analysis": snapshot_doc.get("analysis", {}),
            "updated_at": utcnow(),
        }
        self.db.candidate_github.update_one({"candidate_id": candidate_id}, {"$set": profile_doc}, upsert=True)
        return snapshot_doc

    def get_latest_github_profile(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        return self.db.candidate_github.find_one({"candidate_id": candidate_id})

    def get_github_evidence_by_skill(self, candidate_id: str, skill_name: str) -> Optional[Dict[str, Any]]:
        clean_skill = skill_name.strip().casefold()
        profile = self.get_latest_github_profile(candidate_id) or {}
        for item in profile.get("skill_evidence") or []:
            if item.get("skill", "").strip().casefold() == clean_skill:
                return item
        return None
