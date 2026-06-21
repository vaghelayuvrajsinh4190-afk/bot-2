"""
Mack Bot Tortuga — Group Model
CRUD operations for daily groups.
Groups are pre-created by the provisioning job and filled atomically.
"""

import datetime
from pymongo import ReturnDocument
from database import groups

def create_group(event_id: str, group_id: str, capacity: int,
                 match1: dict, match2: dict,
                 channel_id: int, role_id: int, category_id: int):
    """
    Insert a new group document.

    Args:
        event_id: Date-based event ID (e.g. "2026-06-20")
        group_id: Group identifier (e.g. "G0001")
        capacity: Max teams in this group (e.g. 21)
        match1: Dict with {idp, start, map} for match 1
        match2: Dict with {idp, start, map} for match 2
        channel_id: Discord channel ID for this group
        role_id: Discord role ID for this group
        category_id: Discord category ID this group belongs to
    """
    doc = {
        "event_id": event_id,
        "group_id": group_id,
        "capacity": capacity,
        "current_count": 0,
        "match1": match1,
        "match2": match2,
        "channel_id": channel_id,
        "role_id": role_id,
        "category_id": category_id,
        "roster_message_id": None,  # ID of the live roster embed message
        "slot_availability_message_id": None,
        "archived": False,
        "locked": False,  # True when T-20min lock fires
        "reminder_sent": False,
        "slot_list_published": False,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    # Upsert instead of insert_one: a previously deprovisioned/archived doc
    # for this (event_id, group_id) may still exist (archiving sets
    # archived=True but never deletes), and the unique index on
    # (event_id, group_id) would reject a plain insert in that case.
    groups.replace_one(
        {"event_id": event_id, "group_id": group_id},
        doc,
        upsert=True
    )
    return doc

def claim_slot(event_id: str):
    """
    Atomically claim a slot in the lowest-numbered group with room.
    Uses findOneAndUpdate to prevent race conditions.

    Returns the updated group doc, or None if all groups are full.
    """
    result = groups.find_one_and_update(
        {
            "event_id": event_id,
            "archived": {"$ne": True},
            "locked": {"$ne": True},
            "$expr": {"$lt": ["$current_count", "$capacity"]}
        },
        {"$inc": {"current_count": 1}},
        sort=[("group_id", 1)],  # lowest group_id with room wins
        return_document=ReturnDocument.AFTER
    )
    return result

def release_slot(event_id: str, group_id: str):
    """
    Decrement the count when a team cancels.
    Returns the updated group doc.
    """
    result = groups.find_one_and_update(
        {"event_id": event_id, "group_id": group_id},
        {"$inc": {"current_count": -1}},
        return_document=ReturnDocument.AFTER
    )
    return result

def move_slot(event_id: str, from_group_id: str, to_group_id: str):
    """
    Atomically move a slot from one group to another.
    Returns (old_group_doc, new_group_doc) or (None, None) if target is full.
    """
    # First try to claim the target
    new_group = groups.find_one_and_update(
        {
            "event_id": event_id,
            "group_id": to_group_id,
            "archived": {"$ne": True},
            "locked": {"$ne": True},
            "$expr": {"$lt": ["$current_count", "$capacity"]}
        },
        {"$inc": {"current_count": 1}},
        return_document=ReturnDocument.AFTER
    )
    if not new_group:
        return None, None

    # Release the old slot
    old_group = release_slot(event_id, from_group_id)
    return old_group, new_group

def get_group(event_id: str, group_id: str):
    """Get a specific group document."""
    return groups.find_one({"event_id": event_id, "group_id": group_id})

def get_all_groups(event_id: str, include_archived=False):
    """Get all groups for an event, sorted by group_id."""
    query = {"event_id": event_id}
    if not include_archived:
        query["archived"] = {"$ne": True}
    return list(groups.find(query).sort("group_id", 1))

def get_open_groups(event_id: str):
    """Get groups that still have room and aren't locked."""
    return list(groups.find({
        "event_id": event_id,
        "archived": {"$ne": True},
        "locked": {"$ne": True},
        "$expr": {"$lt": ["$current_count", "$capacity"]}
    }).sort("group_id", 1))

def archive_groups(event_id: str):
    """Mark all groups for an event as archived (nightly cleanup)."""
    result = groups.update_many(
        {"event_id": event_id},
        {"$set": {"archived": True}}
    )
    return result.modified_count

def lock_group(event_id: str, group_id: str):
    """Lock a group (no more cancel/reschedule allowed)."""
    groups.update_one(
        {"event_id": event_id, "group_id": group_id},
        {"$set": {"locked": True}}
    )

def set_reminder_sent(event_id: str, group_id: str):
    """Mark reminder as sent for a group."""
    groups.update_one(
        {"event_id": event_id, "group_id": group_id},
        {"$set": {"reminder_sent": True}}
    )

def set_slot_list_published(event_id: str, group_id: str):
    """Mark slot list as published for a group."""
    groups.update_one(
        {"event_id": event_id, "group_id": group_id},
        {"$set": {"slot_list_published": True}}
    )

def update_roster_message(event_id: str, group_id: str, message_id: int):
    """Store the roster embed message ID for live updates."""
    groups.update_one(
        {"event_id": event_id, "group_id": group_id},
        {"$set": {"roster_message_id": message_id}}
    )

def update_match_details(event_id: str, group_id: str, match_num: int, details: dict):
    """
    Update match details (IDP, start, map) for a specific match.
    match_num: 1 or 2
    details: dict with keys like {idp, start, map}
    """
    field = f"match{match_num}"
    update = {f"{field}.{k}": v for k, v in details.items()}
    groups.update_one(
        {"event_id": event_id, "group_id": group_id},
        {"$set": update}
    )
