"""
Mack Bot — Scrim Model
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CRUD operations and configuration management for the multi-scrim system.

Each scrim document represents a fully independent tournament tier with its
own schedule, groups, registrations, points, channels, and module toggles.

Key Functions:
    create_scrim / delete_scrim   —  Lifecycle management
    get_scrim / get_all_scrims    —  Document retrieval
    set_scrim_setting / module    —  Granular configuration
    set_scrim_channel             —  Per-scrim channel overrides
    is_group_started_or_finished  —  Time-based match validation
"""

import datetime
import copy
import json
import os
import traceback
from database import scrims as scrims_collection
from config import DEFAULT_GROUP_CAPACITY, DEFAULT_GROUP_COUNT, DEFAULT_RESERVED_SLOTS, DEFAULT_POSITION_POINTS, TIMEZONE_OFFSET


# ═══════════════════ DEFAULT SCRIM TEMPLATE ═══════════════════

DEFAULT_SETTINGS = {
    "capacity": DEFAULT_GROUP_CAPACITY,
    "group_count": DEFAULT_GROUP_COUNT,
    "reserved_slots": DEFAULT_RESERVED_SLOTS,
    "category_name": "📋 SCRIMS",
    "registration_category_id": None,
    "registration_category_name": None,
    "group_naming_format": "{scrim_id} Group {number:02d}",
    "starting_number": 1,
    "permission_template_id": None,
    "registration_open_hour": 10,
    "registration_open_minute": 0,
    "lock_minutes": 20,
    "reminder_lead_minutes": 30,
    "timezone_offset": 5.5,
    "channel_mode": "shared",  # "shared" or "separate"
    "cross_tier_registration": False,  # Allow registering in multiple tiers same day
    "create_group_channels": True,
}

DEFAULT_MODULES = {
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
    "autopilot": True,
}

DEFAULT_CHANNELS = {
    "register": None,
    "admin_log": None,
    "leaderboard": None,
    "registered_teams": None,
    "results": None,
    "announcements": None,
}

DEFAULT_POINTS_CONFIG = {
    "kill_points": 1,
    "position_points": copy.deepcopy(DEFAULT_POSITION_POINTS),
}


# ═══════════════════ CRUD ═══════════════════


def create_scrim(scrim_id: str, name: str, owner_id: str,
                 description: str = "", embed_color: str = "#BF5AF2",
                 logo_url: str = None, banner_url: str = None,
                 settings: dict = None, modules: dict = None,
                 schedule: list = None, points_config: dict = None):
    """
    Create a new scrim.

    Args:
        scrim_id: Unique identifier (e.g. "SQ", "T3")
        name: Display name (e.g. "SQ Scrims")
        owner_id: Discord user ID of the creator
        description: Optional description
        embed_color: Hex color for embeds
        logo_url: Optional thumbnail URL
        banner_url: Optional banner URL
        settings: Override default settings
        modules: Override default modules
        schedule: Group schedule data
        points_config: Override default points
    """
    now = datetime.datetime.utcnow().isoformat()

    scrim_settings = copy.deepcopy(DEFAULT_SETTINGS)
    if settings:
        scrim_settings.update(settings)

    scrim_modules = copy.deepcopy(DEFAULT_MODULES)
    if modules:
        scrim_modules.update(modules)

    scrim_points = copy.deepcopy(DEFAULT_POINTS_CONFIG)
    if points_config:
        scrim_points.update(points_config)

    doc = {
        "scrim_id": scrim_id.upper(),
        "name": name,
        "description": description,
        "status": "active",
        "embed_color": embed_color,
        "logo_url": logo_url,
        "banner_url": banner_url,
        "owner_id": owner_id,
        "created_at": now,
        "updated_at": now,
        "settings": scrim_settings,
        "modules": scrim_modules,
        "channels": copy.deepcopy(DEFAULT_CHANNELS),
        "schedule": schedule or [],
        "points_config": scrim_points,
    }

    scrims_collection.update_one(
        {"scrim_id": scrim_id.upper()},
        {"$setOnInsert": doc},
        upsert=True
    )
    return doc


def get_scrim(scrim_id: str):
    """Get a scrim by its unique ID."""
    return scrims_collection.find_one({"scrim_id": scrim_id.upper()})


def ensure_scrim_exists(scrim_id: str, owner_id: str = "system"):
    """Ensure a scrim document exists in DB, creating it if missing."""
    scrim_id_upper = scrim_id.upper()
    existing = get_scrim(scrim_id_upper)
    if not existing:
        name_map = {
            "SQ": "SQ Scrims",
            "T3": "Tier 3 Scrims",
            "T2": "Tier 2 Scrims",
            "T1": "Tier 1 Scrims",
        }
        display_name = name_map.get(scrim_id_upper, f"{scrim_id_upper} Scrims")
        return create_scrim(
            scrim_id=scrim_id_upper,
            name=display_name,
            owner_id=owner_id,
            description=f"Auto-created {display_name}"
        )
    return existing



def get_all_scrims():
    """Get all scrims, sorted by creation date."""
    return list(scrims_collection.find({}).sort("created_at", 1))


def get_active_scrims():
    """Get all active (non-archived, non-disabled) scrims."""
    return list(scrims_collection.find({"status": "active"}).sort("created_at", 1))


def update_scrim(scrim_id: str, updates: dict):
    """
    Update scrim fields.
    Supports dot-notation for nested updates (e.g. {"settings.capacity": 15}).
    """
    updates["updated_at"] = datetime.datetime.utcnow().isoformat()
    scrims_collection.update_one(
        {"scrim_id": scrim_id.upper()},
        {"$set": updates}
    )


def delete_scrim(scrim_id: str):
    """Permanently delete a scrim. Use archive_scrim() to preserve data."""
    result = scrims_collection.delete_one({"scrim_id": scrim_id.upper()})
    return result.deleted_count > 0


def duplicate_scrim(source_scrim_id: str, new_scrim_id: str, new_name: str, owner_id: str):
    """
    Clone a scrim with a new ID and name.
    Copies all settings, modules, schedule, and points config.
    Channels are reset to None (not copied).
    """
    source = get_scrim(source_scrim_id)
    if not source:
        return None

    return create_scrim(
        scrim_id=new_scrim_id,
        name=new_name,
        owner_id=owner_id,
        description=source.get("description", ""),
        embed_color=source.get("embed_color", "#BF5AF2"),
        logo_url=source.get("logo_url"),
        banner_url=source.get("banner_url"),
        settings=copy.deepcopy(source.get("settings", {})),
        modules=copy.deepcopy(source.get("modules", {})),
        schedule=copy.deepcopy(source.get("schedule", [])),
        points_config=copy.deepcopy(source.get("points_config", {})),
    )


def archive_scrim(scrim_id: str):
    """Archive a scrim (preserve data, stop operations)."""
    update_scrim(scrim_id, {"status": "archived"})


def enable_scrim(scrim_id: str):
    """Enable a disabled/archived scrim."""
    update_scrim(scrim_id, {"status": "active"})


def disable_scrim(scrim_id: str):
    """Temporarily disable a scrim (preserve data, stop operations)."""
    update_scrim(scrim_id, {"status": "disabled"})


# ═══════════════════ SETTINGS HELPERS ═══════════════════


def get_scrim_setting(scrim_id: str, key: str, default=None):
    """Get a specific setting from a scrim's settings dict."""
    scrim = get_scrim(scrim_id)
    if not scrim:
        return default
    return scrim.get("settings", {}).get(key, default)


def set_scrim_setting(scrim_id: str, key: str, value):
    """Set a specific setting in a scrim's settings dict."""
    update_scrim(scrim_id, {f"settings.{key}": value})


def get_scrim_module(scrim_id: str, module: str):
    """Check if a module is enabled for a scrim."""
    scrim = get_scrim(scrim_id)
    if not scrim:
        return False
    return scrim.get("modules", {}).get(module, False)


def set_scrim_module(scrim_id: str, module: str, enabled: bool):
    """Enable or disable a module for a scrim."""
    update_scrim(scrim_id, {f"modules.{module}": enabled})


def get_scrim_channel(scrim_id: str, channel_type: str):
    """
    Get the configured channel ID for a scrim.
    Returns None if the scrim uses shared channels (falls back to global).
    """
    scrim = get_scrim(scrim_id)
    if not scrim:
        return None
    return scrim.get("channels", {}).get(channel_type)


def set_scrim_channel(scrim_id: str, channel_type: str, channel_id: int):
    """Set a channel ID for a specific scrim."""
    update_scrim(scrim_id, {f"channels.{channel_type}": channel_id})


def get_scrim_schedule(scrim_id: str):
    """Get the schedule for a scrim."""
    scrim = get_scrim(scrim_id)
    if not scrim:
        return []
    return scrim.get("schedule", [])


def set_scrim_schedule(scrim_id: str, schedule: list):
    """Set the schedule for a scrim."""
    update_scrim(scrim_id, {"schedule": schedule})


def get_scrim_points_config(scrim_id: str):
    """Get points configuration for a scrim."""
    scrim = get_scrim(scrim_id)
    if not scrim:
        return copy.deepcopy(DEFAULT_POINTS_CONFIG)
    return scrim.get("points_config", copy.deepcopy(DEFAULT_POINTS_CONFIG))


def get_scrim_color(scrim_id: str):
    """Get the embed color for a scrim as a discord.Color-compatible int."""
    scrim = get_scrim(scrim_id)
    if not scrim:
        return None
    hex_color = scrim.get("embed_color", "#BF5AF2")
    try:
        return int(hex_color.lstrip("#"), 16)
    except (ValueError, AttributeError):
        return 0xBF5AF2  # Default purple


# ═══════════════════ TIME VALIDATION ═══════════════════


def is_group_started_or_finished(scrim_id: str, group_number: int) -> bool:
    """
    Check if a group's match has already started or finished.
    Returns True if current IST time > match time.
    """
    try:
        from config import load_schedule
        schedule = load_schedule(scrim_id)
        if not schedule:
            return False

        target_group = None
        for g in schedule:
            if g.get("group_number") == group_number:
                target_group = g
                break

        if not target_group:
            return False

        start_str = target_group.get("match1", {}).get("start")
        if not start_str or start_str.upper() == "TBD":
            return False

        # Parse "HH:MM AM/PM" into today's datetime with IST offset
        utc_now = datetime.datetime.utcnow()
        ist_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)

        try:
            parsed_time = datetime.datetime.strptime(start_str.strip(), "%I:%M %p")
        except ValueError:
            # Silently ignore format errors (user typed the time wrong in schedule)
            return False

        match_dt = ist_now.replace(
            hour=parsed_time.hour,
            minute=parsed_time.minute,
            second=0,
            microsecond=0
        )

        return ist_now > match_dt

    except Exception as e:
        print(f"⚠️ is_group_started_or_finished error: {e}", flush=True)
        return False
