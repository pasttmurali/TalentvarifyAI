"""
MongoDB repository for jobs, requirements, applications, and evaluations.

This file provides the Data Access Layer (DAL) for:
1. 'jobs' collection: Job postings and recruiter requirements
2. 'applications' collection: Candidate applications and AI match evaluations
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pymongo.database import Database


def utcnow():
    """Helper function to get current timestamp in UTC timezone."""
    return datetime.now(timezone.utc)


class JobRepository:
    """Repository class encapsulating all MongoDB database calls for jobs and applications."""

    def __init__(self, db: Database):
        # Store database connection reference
        self.db = db

    def ensure_indexes(self):
        """
        Create database indexes for fast querying and data integrity.
        - jobs 'id': Fast O(1) lookup by job ID
        - jobs 'recruiterId': Fast filtering of jobs belonging to a specific recruiter
        - applications '(userId, jobId)': Compound UNIQUE index that prevents a candidate
          from accidentally applying to the exact same job twice!
        """
        self.db.jobs.create_index("id", unique=True)
        self.db.jobs.create_index("recruiterId")
        self.db.applications.create_index([("userId", 1), ("jobId", 1)], unique=True)
        self.db.applications.create_index("id", unique=True)

    def find_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Query a single job document by its unique job ID.
        Returns the job document dictionary or None if not found.
        """
        return self.db.jobs.find_one({"id": job_id})

    def find_all_jobs(self) -> List[Dict[str, Any]]:
        """
        Query all jobs in the database, sorted newest-first by creation date (createdAt descending).
        """
        return list(self.db.jobs.find().sort("createdAt", -1))

    def save_application(self, application_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Save a new candidate job application or update an existing one.
        Uses upsert=True to create the document if it doesn't exist yet.
        """
        self.db.applications.update_one({"id": application_doc["id"]}, {"$set": application_doc}, upsert=True)
        return application_doc

    def get_application_by_id(self, application_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific application document by its application ID.
        """
        return self.db.applications.find_one({"id": application_id})

