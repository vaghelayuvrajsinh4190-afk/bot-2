"""
Mack Bot Tortuga — Registration Model
CRUD operations for daily team registrations.
Each registration links a team to a group for one day's event.
"""

import datetime
from database import registrations


def create_registration(owner_id: str, event_id: str, group_id: str,
                        team_name: str, players: list, teammate_ids: list):
    """
    Insert a new registration.
    
    Args:
        owner_id: Discord user ID (captain)
        event_id: Date-based event ID (e.g. "2026-06-20")
        group_id: Assigned group identifier
        team_name: Team display name
        players: List of player in-game names
        teammate_ids: List of Discord user IDs of teammates
    """
    doc = {
        "owner_id": owner_id,
        "event_id": event_id,
        "group_id": group_id,
        "team_name": team_name,
        "players": players,
        "teammate_ids": teammate_ids,
        "status": "registered",  # registered | cancelled | no_show
        "ss_submitted": False,
        "registered_at": datetime.datetime.utcnow().isoformat()
    }
    registrations.insert_one(doc)
    return doc


def get_registration(owner_id: str, event_id: str):
    """Get a user's registration for today's event."""
    return registrations.find_one({
        "owner_id": owner_id,
        "event_id": event_id,
        "status": "registered"
    })


def get_group_registrations(group_id: str, event_id: str):
    """Get all active registrations for a group, in registration order."""
    return list(registrations.find({
        "group_id": group_id,
        "event_id": event_id,
        "status": "registered"
    }).sort("registered_at", 1))


def cancel_registration(owner_id: str, event_id: str):
    """
    Cancel a registration (mark as cancelled).
    Returns the cancelled doc or None.
    """
    result = registrations.find_one_and_update(
        {
            "owner_id": owner_id,
            "event_id": event_id,
            "status": "registered"
        },
        {"$set": {
            "status": "cancelled",
            "cancelled_at": datetime.datetime.utcnow().isoformat()
        }}
    )
    return result


def move_registration(owner_id: str, event_id: str, new_group_id: str):
    """
    Move a registration to a different group.
    Returns the old group_id or None if not found.
    """
    doc = registrations.find_one({
        "owner_id": owner_id,
        "event_id": event_id,
        "status": "registered"
    })
    if not doc:
        return None

    old_group_id = doc["group_id"]
    registrations.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "group_id": new_group_id,
            "moved_at": datetime.datetime.utcnow().isoformat()
        }}
    )
    return old_group_id


def mark_ss_submitted(owner_id: str, event_id: str):
    """Mark that the team submitted their screenshot."""
    registrations.update_one(
        {"owner_id": owner_id, "event_id": event_id, "status": "registered"},
        {"$set": {"ss_submitted": True}}
    )


def mark_no_show(owner_id: str, event_id: str):
    """Mark a team as a no-show."""
    registrations.update_one(
        {"owner_id": owner_id, "event_id": event_id, "status": "registered"},
        {"$set": {"status": "no_show"}}
    )


def get_all_registrations(event_id: str, status="registered"):
    """Get all registrations for an event with a given status."""
    return list(registrations.find({
        "event_id": event_id,
        "status": status
    }))


def count_registrations(event_id: str, status="registered"):
    """Count registrations for an event."""
    return registrations.count_documents({
        "event_id": event_id,
        "status": status
    })


def is_already_registered(owner_id: str, event_id: str):
    """Check if a user is already registered for today."""
    return registrations.find_one({
        "owner_id": owner_id,
        "event_id": event_id,
        "status": "registered"
    }) is not None


def is_teammate_registered(user_id: str, event_id: str):
    """
    Check if a user is already part of any team (as owner or teammate) for today.
    Returns (is_registered, team_name or None).
    """
    # Check as owner
    as_owner = registrations.find_one({
        "owner_id": user_id,
        "event_id": event_id,
        "status": "registered"
    })
    if as_owner:
        return True, as_owner.get("team_name")

    # Check as teammate
    as_teammate = registrations.find_one({
        "teammate_ids": user_id,
        "event_id": event_id,
        "status": "registered"
    })
    if as_teammate:
        return True, as_teammate.get("team_name")

    return False, None
