"""
Mack Bot — Registration Model
CRUD operations for daily team registrations.
Each registration links a team to a group for one day's event.
"""

import datetime
from database import registrations


def create_registration(owner_id: str, event_id: str, group_id: str,
                        team_name: str, players: list, teammate_ids: list,
                        slot_number: int = None, scrim_id: str = None):
    """
    Insert a new registration.
    
    Args:
        owner_id: Discord user ID (captain)
        event_id: Date-based event ID (e.g. "2026-06-20")
        group_id: Assigned group identifier
        team_name: Team display name
        players: List of player in-game names
        teammate_ids: List of Discord user IDs of teammates
        slot_number: The roster slot number assigned to this team
        scrim_id: Scrim identifier (required)
    """
    doc = {
        "scrim_id": scrim_id,
        "owner_id": owner_id,
        "event_id": event_id,
        "group_id": group_id,
        "team_name": team_name,
        "players": players,
        "teammate_ids": teammate_ids,
        "status": "registered",  # registered | cancelled | no_show
        "ss_submitted": False,
        "dm_reminder": False,
        "slot_number": slot_number,
        "registered_at": datetime.datetime.utcnow().isoformat()
    }
    result = registrations.update_one(
        {"owner_id": owner_id, "event_id": event_id},
        {"$setOnInsert": doc},
        upsert=True
    )
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


def move_registration(owner_id: str, event_id: str, new_group_id: str, new_slot_number: int = None):
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
    update_fields = {
        "group_id": new_group_id,
        "moved_at": datetime.datetime.utcnow().isoformat()
    }
    if new_slot_number is not None:
        update_fields["slot_number"] = new_slot_number

    registrations.update_one(
        {"_id": doc["_id"]},
        {"$set": update_fields}
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


def get_all_registrations(event_id: str, status="registered", scrim_id: str = None):
    """Get all registrations for an event with a given status, optionally filtered by scrim_id."""
    query = {
        "event_id": event_id,
        "status": status
    }
    if scrim_id:
        query["scrim_id"] = scrim_id
    return list(registrations.find(query))


def count_registrations(event_id: str, status="registered", scrim_id: str = None):
    """Count registrations for an event, optionally filtered by scrim_id."""
    query = {
        "event_id": event_id,
        "status": status
    }
    if scrim_id:
        query["scrim_id"] = scrim_id
    return registrations.count_documents(query)


def is_already_registered(owner_id: str, event_id: str, cross_tier_check_date: str = None):
    """Check if a user is already registered for today."""
    query = {
        "owner_id": owner_id,
        "status": "registered"
    }
    if cross_tier_check_date:
        # Match any event_id ending with the date (e.g., "T3_2026-08-01" or "SQ_2026-08-01")
        query["event_id"] = {"$regex": f"{cross_tier_check_date}$"}
    else:
        query["event_id"] = event_id

    return registrations.find_one(query) is not None


def is_teammate_registered(user_id: str, event_id: str, cross_tier_check_date: str = None):
    """
    Check if a user is already part of any team (as owner or teammate) for today.
    Returns (is_registered, team_name or None).
    """
    query_base = {"status": "registered"}
    if cross_tier_check_date:
        query_base["event_id"] = {"$regex": f"{cross_tier_check_date}$"}
    else:
        query_base["event_id"] = event_id

    # Check as owner
    owner_query = query_base.copy()
    owner_query["owner_id"] = user_id
    as_owner = registrations.find_one(owner_query)
    if as_owner:
        return True, as_owner.get("team_name")

    # Check as teammate
    teammate_query = query_base.copy()
    teammate_query["teammate_ids"] = user_id
    as_teammate = registrations.find_one(teammate_query)
    if as_teammate:
        return True, as_teammate.get("team_name")

    return False, None
