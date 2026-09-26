import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from bson import ObjectId

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repositories.candidate_repository import CandidateRepository


class FakeCollection:
    def __init__(self, existing=None):
        self.existing = existing

    def find_one(self, *_args, **_kwargs):
        return self.existing

    def find_one_and_update(self, query, update, **_kwargs):
        return {"_id": ObjectId(), **update["$set"]}

    def insert_one(self, document):
        document["_id"] = ObjectId()
        return SimpleNamespace(inserted_id=document["_id"])

    def update_one(self, *_args, **_kwargs):
        return SimpleNamespace()


class CandidateRepositoryTests(unittest.TestCase):
    def make_db(self, existing=None):
        return SimpleNamespace(
            candidate_profiles=FakeCollection(existing),
            candidates=FakeCollection(),
        )

    def test_new_profile_does_not_return_inserted_object_id(self):
        repository = CandidateRepository(self.make_db())
        result = repository.save_or_update_profile(
            "candidate-1",
            {"personal_info": {"full_name": "Thanujan"}},
        )
        self.assertNotIn("_id", result)
        self.assertEqual("candidate-1", result["id"])

    def test_updated_profile_does_not_return_mongo_object_id(self):
        existing = {
            "_id": ObjectId(),
            "id": "candidate-1",
            "user_id": "candidate-1",
            "personal_info": {},
            "social_links": {},
        }
        repository = CandidateRepository(self.make_db(existing))
        result = repository.save_or_update_profile(
            "candidate-1",
            {"personal_info": {"full_name": "Thanujan"}},
        )
        self.assertNotIn("_id", result)
        self.assertEqual("Thanujan", result["personal_info"]["full_name"])


if __name__ == "__main__":
    unittest.main()
