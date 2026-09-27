"""
MongoDB connection module shared by all FastAPI routes.

This file handles connecting to the MongoDB database where all application data
(users, candidates, jobs, applications, audit logs) is safely stored.
"""

import os
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

# STEP 1: Load environment variables from backend/.env file (e.g., MONGODB_URI, MONGODB_DB)
load_dotenv()

# STEP 2: Retrieve MongoDB connection string and database name with safe fallback defaults
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "talentverify")

# STEP 3: Initialize the PyMongo client connection pool
# - serverSelectionTimeoutMS=5000: If MongoDB isn't running, fail in 5 seconds instead of hanging
# - appname="TalentVerifyAI": Identifies this application in MongoDB server logs
# - tz_aware=True: Ensures all stored dates preserve UTC timezone accuracy
mongo_options = {
    "serverSelectionTimeoutMS": 5000,
    "appname": "TalentVerifyAI",
    "tz_aware": True,
}

# Atlas requires TLS. Use Certifi for this connection instead of globally
# replacing Python's SSLContext, which can recurse on managed runtimes.
if MONGODB_URI.casefold().startswith("mongodb+srv://"):
    mongo_options["tlsCAFile"] = certifi.where()

mongo_client = MongoClient(MONGODB_URI, **mongo_options)

# STEP 4: Access the primary database instance ('talentverify')
db = mongo_client[MONGODB_DB]


def connect_database() -> None:
    """
    Ping MongoDB to verify active database connectivity.
    If MongoDB is down, this raises a connection error immediately (Fail-Fast).
    """
    mongo_client.admin.command("ping")


def close_database() -> None:
    """
    Gracefully close the MongoDB client connection pool when backend shuts down.
    """
    mongo_client.close()
