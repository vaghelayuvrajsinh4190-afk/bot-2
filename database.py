"""
Mack Bot — Database Layer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MongoDB connection management, collection references, index creation,
and data-migration utilities.

Collections:
    team_profiles   —  Persistent team profiles (30-day TTL)
    groups          —  Daily group slot allocations
    registrations   —  Daily team registration records
    punishments     —  Bans, strikes, and disciplinary records
    bot_config      —  Key-value bot settings store
    match_results   —  Points and leaderboard data
    scrims          —  Dynamic multi-scrim configurations
    global_teams    —  Cross-tier team tracking and global statistics
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
        client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=10000,
            maxPoolSize=50,
            minPoolSize=10,
            retryWrites=True
        )
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
scrims         = db["scrims"]            # Dynamic scrim configurations
global_teams   = db["global_teams"]      # Cross-tier team tracking & stats


# ═══════════════════ INDEXES ═══════════════════

def create_indexes():
    """Create all necessary indexes on startup."""
    print("📇 Creating database indexes...", flush=True)

    # team_profiles: lookup by owner_id, TTL cleanup by expires_at
    team_profiles.create_index("owner_id", unique=True)
    team_profiles.create_index("expires_at")

    # groups: lookup by event_id + group_id, find open groups
    # Legacy index (kept for backward compat)
    groups.create_index([("event_id", ASCENDING), ("group_id", ASCENDING)], unique=True)
    groups.create_index([("event_id", ASCENDING), ("archived", ASCENDING), ("current_count", ASCENDING)])
    # Multi-scrim indexes
    groups.create_index([("scrim_id", ASCENDING), ("event_id", ASCENDING), ("group_id", ASCENDING)])
    groups.create_index([("scrim_id", ASCENDING), ("event_id", ASCENDING), ("archived", ASCENDING)])

    # registrations: lookup by owner_id + event_id, by group
    registrations.create_index([("owner_id", ASCENDING), ("event_id", ASCENDING)], unique=True)
    registrations.create_index([("group_id", ASCENDING), ("event_id", ASCENDING)])
    registrations.create_index("status")
    # Multi-scrim indexes
    registrations.create_index([("scrim_id", ASCENDING), ("owner_id", ASCENDING), ("event_id", ASCENDING)])
    registrations.create_index([("scrim_id", ASCENDING), ("group_id", ASCENDING), ("event_id", ASCENDING)])

    # punishments: lookup by owner_id
    punishments.create_index("owner_id")
    punishments.create_index("expires_at")
    # Multi-scrim index
    punishments.create_index([("scrim_id", ASCENDING), ("owner_id", ASCENDING)])

    # bot_config: lookup by key
    bot_config.create_index("key", unique=True)

    # match_results: lookup by event_id + group_id
    match_results.create_index([("event_id", ASCENDING), ("group_id", ASCENDING)])
    # Multi-scrim index
    match_results.create_index([("scrim_id", ASCENDING), ("event_id", ASCENDING), ("group_id", ASCENDING)])

    # scrims: lookup by scrim_id
    scrims.create_index("scrim_id", unique=True)
    scrims.create_index("status")

    # global_teams: lookup by owner_id, by tier, leaderboard sorting
    global_teams.create_index("owner_id", unique=True)
    global_teams.create_index("team_key")
    global_teams.create_index("current_tier")
    global_teams.create_index([("current_tier", ASCENDING), ("total_points", DESCENDING)])

    print("✅ Database indexes ready.", flush=True)


# ═══════════════════ DATA MIGRATION ═══════════════════

def migrate_existing_data():
    """
    Migrate existing single-scrim data to the multi-scrim system.
    
    - Adds scrim_id="SQ" to all existing documents that lack it
    - Creates the default SQ scrim document from current config
    - Idempotent: safe to run multiple times
    """
    already_migrated = get_config("multi_scrim_migrated", False)
    if already_migrated:
        return

    print("🔄 Migrating existing data to multi-scrim system...", flush=True)

    # Add scrim_id="SQ" to all documents missing it
    collections_to_migrate = [
        (groups, "groups"),
        (registrations, "registrations"),
        (match_results, "match_results"),
        (punishments, "punishments"),
    ]

    total_migrated = 0
    for collection, name in collections_to_migrate:
        result = collection.update_many(
            {"scrim_id": {"$exists": False}},
            {"$set": {"scrim_id": "SQ"}}
        )
        if result.modified_count > 0:
            print(f"  📦 Migrated {result.modified_count} documents in '{name}'", flush=True)
            total_migrated += result.modified_count

    # Create default SQ scrim document if it doesn't exist
    existing_sq = scrims.find_one({"scrim_id": "SQ"})
    if not existing_sq:
        from config import (
            DEFAULT_GROUP_CAPACITY, DEFAULT_GROUP_COUNT, DEFAULT_RESERVED_SLOTS,
            REGISTRATION_OPEN_HOUR, REGISTRATION_OPEN_MINUTE,
            DEFAULT_LOCK_MINUTES, DEFAULT_REMINDER_LEAD_MINUTES,
            TIMEZONE_OFFSET, DEFAULT_CATEGORY_NAME,
            DEFAULT_POSITION_POINTS, load_schedule
        )
        import datetime
        import copy

        # Load existing schedule
        existing_schedule = load_schedule()

        # Load existing points config from db
        kill_points = get_config("kill_points", 1)
        position_points = get_config("position_points", copy.deepcopy(DEFAULT_POSITION_POINTS))

        sq_doc = {
            "scrim_id": "SQ",
            "name": "SQ Scrims",
            "description": "Squad Qualifiers — the original scrim tier",
            "status": "active",
            "embed_color": "#BF5AF2",
            "logo_url": None,
            "banner_url": None,
            "owner_id": "system",
            "created_at": datetime.datetime.utcnow().isoformat(),
            "updated_at": datetime.datetime.utcnow().isoformat(),
            "settings": {
                "capacity": int(get_config("default_group_capacity", DEFAULT_GROUP_CAPACITY)),
                "group_count": int(get_config("default_group_count", DEFAULT_GROUP_COUNT)),
                "reserved_slots": int(get_config("default_reserved_slots", DEFAULT_RESERVED_SLOTS)),
                "category_name": get_config("default_category_name", DEFAULT_CATEGORY_NAME),
                "registration_open_hour": int(get_config("registration_open_hour", REGISTRATION_OPEN_HOUR)),
                "registration_open_minute": int(get_config("registration_open_minute", REGISTRATION_OPEN_MINUTE)),
                "lock_minutes": int(get_config("lock_minutes", DEFAULT_LOCK_MINUTES)),
                "reminder_lead_minutes": int(get_config("reminder_lead_minutes", DEFAULT_REMINDER_LEAD_MINUTES)),
                "timezone_offset": TIMEZONE_OFFSET,
                "channel_mode": "shared",
            },
            "modules": {
                "registration": True,
                "schedule": True,
                "teams": True,
                "slot_list": True,
                "points": True,
                "leaderboard": True,
                "results": True,
                "reminders": True,
                "verification": False,
                "check_in": False,
                "auto_reset": True,
                "auto_room_distribution": False,
                "logging": True,
                "announcements": True,
                "voice_channels": False,
                "match_rooms": False,
                "auto_backup": False,
                "auto_archive": False,
                "auto_leaderboard": False,
                "auto_results": False,
            },
            "channels": {
                "register": get_config("channel_register"),
                "admin_log": get_config("channel_admin_log"),
                "leaderboard": get_config("channel_leaderboard"),
                "registered_teams": get_config("channel_registered_teams"),
                "results": None,
                "announcements": None,
            },
            "schedule": existing_schedule,
            "points_config": {
                "kill_points": kill_points,
                "position_points": position_points,
            },
        }

        scrims.insert_one(sq_doc)
        print("  ✅ Created default SQ scrim document", flush=True)

    set_config("multi_scrim_migrated", True)
    print(f"✅ Migration complete! ({total_migrated} documents updated)", flush=True)


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
