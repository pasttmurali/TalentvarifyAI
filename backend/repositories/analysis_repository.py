"""MongoDB repository for analysis runs, candidate scores, and job matches."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pymongo.database import Database


def utcnow():
    return datetime.now(timezone.utc)


class AnalysisRepository:
    def __init__(self, db: Database):
        self.db = db

    def ensure_indexes(self):
        self.db.audit_logs.create_index([("entity_type", 1), ("entity_id", 1), ("created_at", -1)])

    def record_analysis_run(self, run_doc: Dict[str, Any]) -> Dict[str, Any]:
        stamp = utcnow()
        run_doc.setdefault("analysis_timestamp", stamp)
        self.db.audit_logs.insert_one({
            "action": "analysis_run_recorded",
            "actor_id": run_doc.get("candidate_id"),
            "actor_role": "candidate",
            "entity_type": "analysis_run",
            "entity_id": run_doc.get("id"),
            "details": run_doc,
            "created_at": stamp
        })
        return run_doc

    def get_candidate_analysis_history(self, candidate_id: str) -> List[Dict[str, Any]]:
        return list(self.db.audit_logs.find({"actor_id": candidate_id, "entity_type": "analysis_run"}).sort("created_at", -1))

    def get_candidate_job_match(self, candidate_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        return self.db.applications.find_one({"userId": candidate_id, "jobId": job_id})
