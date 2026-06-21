"""
Mack Bot — Database Connection & Collections
Handles MongoDB connection, collection references, and index creation.
"""

from pymongo import MongoClient, ASCENDING, DESCENDING
from config import MONGO_URI


# ═══════════════════ CONNECTION ═══════════════════

def connect():
    """
    Connect to MongoDB and return (client, db) tuple.
    Validates connection with a ping.
    """
    if not MONGO_URI:
        print("❌ FATAL: MONGO_URI environment variable is not set!", flush=True)
        exit(1)

    if "<db_password>" in MONGO_URI:
        print("❌ FATAL: MONGO_URI still contains '<db_password>' placeholder!", flush=True)
        exit(1)

    print("⏳ Attempting MongoDB connection...", flush=True)
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        client.admin.command("ping")
        print("✅ MongoDB connection successful!", flush=True)
    except Exception as e:
        print(f"❌ FATAL: Could not connect to MongoDB: {e}", flush=True)
        exit(1)

    db = client["mack_db"]
    return client, db


# ═══════════════════ INITIALIZE ═══════════════════

_client, db = connect()

# Collection references
team_profiles  = db["team_profiles"]    # Saved team profiles (persist across days)
groups         = db["groups"]            # Daily group slots
registrations  = db["registrations"]     # Daily team registrations
punishments    = db["punishments"]       # Bans and strikes
bot_config     = db["bot_config"]        # Bot settings (channels, config)
match_results  = db["match_results"]     # Points / leaderboard data


# ═══════════════════ INDEXES ═══════════════════

def create_indexes():
    """Create all necessary indexes on startup."""
    print("📇 Creating database indexes...", flush=True)

    # team_profiles: lookup by owner_id, TTL cleanup by expires_at
    team_profiles.create_index("owner_id", unique=True)
    team_profiles.create_index("expires_at")

    # groups: lookup by event_id + group_id, find open groups
    groups.create_index([("event_id", ASCENDING), ("group_id", ASCENDING)], unique=True)
    groups.create_index([("event_id", ASCENDING), ("archived", ASCENDING), ("current_count", ASCENDING)])

    # registrations: lookup by owner_id + event_id, by group
    registrations.create_index([("owner_id", ASCENDING), ("event_id", ASCENDING)], unique=True)
    registrations.create_index([("group_id", ASCENDING), ("event_id", ASCENDING)])
    registrations.create_index("status")

    # punishments: lookup by owner_id
    punishments.create_index("owner_id")
    punishments.create_index("expires_at")

    # bot_config: lookup by key
    bot_config.create_index("key", unique=True)

    # match_results: lookup by event_id + group_id
    match_results.create_index([("event_id", ASCENDING), ("group_id", ASCENDING)])

    print("✅ Database indexes ready.", flush=True)


# ═══════════════════ BOT CONFIG HELPERS ═══════════════════

def get_config(key, default=None):
    """Get a bot config value by key."""
    doc = bot_config.find_one({"key": key})
    if doc:
        return doc.get("value", default)
    return default


def set_config(key, value):
    """Set a bot config value (upsert)."""
    bot_config.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value}},
        upsert=True
    )


def get_channel_config(channel_type):
    """
    Get a configured channel ID.
    
    channel_type: one of 'register', 'admin', 'admin_log', 'leaderboard'
    """
    return get_config(f"channel_{channel_type}")


def set_channel_config(channel_type, channel_id):
    """Set a channel ID in config."""
    set_config(f"channel_{channel_type}", channel_id)
