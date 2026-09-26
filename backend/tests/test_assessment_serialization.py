import sys
import unittest
from pathlib import Path

from bson import ObjectId
from fastapi.encoders import jsonable_encoder

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assessment_routes import clean


class AssessmentSerializationTests(unittest.TestCase):
    def test_nested_mongo_ids_are_removed_or_serialized(self):
        nested_id = ObjectId()
        value = {
            "_id": ObjectId(),
            "application": {"_id": ObjectId(), "id": "application-1"},
            "ai_evaluation": {
                "_id": ObjectId(),
                "raw_result": {
                    "evidence": [{"source_id": nested_id}, {"_id": ObjectId(), "name": "CV"}],
                },
            },
        }

        result = clean(value)

        self.assertNotIn("_id", result)
        self.assertNotIn("_id", result["application"])
        self.assertNotIn("_id", result["ai_evaluation"])
        self.assertNotIn("_id", result["ai_evaluation"]["raw_result"]["evidence"][1])
        self.assertEqual(str(nested_id), result["ai_evaluation"]["raw_result"]["evidence"][0]["source_id"])
        self.assertEqual(result, jsonable_encoder(result))


if __name__ == "__main__":
    unittest.main()
