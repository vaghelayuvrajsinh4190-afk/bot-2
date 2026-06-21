"""
Mack Bot — Team Profile Model
CRUD operations for saved team profiles.
Teams persist for 30 days so returning players can reuse them.
"""

import datetime
from database import team_profiles
from config import PROFILE_EXPIRY_DAYS


def get_profile(owner_id: str):
    """
    Get a saved team profile by Discord user ID.
    Returns None if profile is expired or doesn't exist.
    """
    profile = team_profiles.find_one({"owner_id": owner_id})
    if not profile:
        return None

    # Check if profile has expired
    expires_at = profile.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.datetime.fromisoformat(expires_at)
            if datetime.datetime.utcnow() > exp_dt:
                # Profile expired — delete it
                team_profiles.delete_one({"owner_id": owner_id})
                return None
        except Exception:
            pass

    return profile


def save_profile(owner_id: str, team_name: str, players: list,
                 teammate_ids: list = None, owner_name: str = None,
                 email: str = None, contact: str = None,
                 player_uids: list = None, player_igns: list = None):
    """
    Create or update a team profile with 30-day expiration.

    Args:
        owner_id: Discord user ID (as string)
        team_name: Team display name
        players: List of player in-game names (legacy field)
        teammate_ids: List of Discord user IDs of teammates
        owner_name: Owner's real name
        email: Contact email
        contact: Contact phone number
        player_uids: List of player UIDs (from Modal 2)
        player_igns: List of player IGNs (from Modal 2)
    """
    now = datetime.datetime.utcnow()
    expires_at = (now + datetime.timedelta(days=PROFILE_EXPIRY_DAYS)).isoformat()

    update_doc = {
        "owner_id": owner_id,
        "team_name": team_name,
        "players": players,
        "teammate_ids": teammate_ids or [],
        "owner_name": owner_name or "",
        "email": email or "",
        "contact": contact or "",
        "player_uids": player_uids or [],
        "player_igns": player_igns or [],
        "updated_at": now.isoformat(),
        "expires_at": expires_at,
    }

    team_profiles.update_one(
        {"owner_id": owner_id},
        {"$set": update_doc},
        upsert=True
    )


def delete_profile(owner_id: str):
    """Delete a team profile."""
    return team_profiles.delete_one({"owner_id": owner_id})


def check_duplicate_team_name(team_name: str, exclude_owner_id: str = None):
    """
    Check if a team name is already taken by another user.
    Returns (is_duplicate, existing_owner_id or None).
    """
    query = {"team_name": {"$regex": f"^{team_name.strip()}$", "$options": "i"}}
    if exclude_owner_id:
        query["owner_id"] = {"$ne": exclude_owner_id}

    existing = team_profiles.find_one(query)
    if existing:
        return True, existing.get("owner_id")
    return False, None


def check_duplicate_player(player_name: str, exclude_owner_id: str = None):
    """
    Check if a player name exists in any other team's roster.
    Returns (is_duplicate, team_name or None).
    """
    clean = player_name.strip().lower()
    query = {"players": {"$regex": f"^{clean}$", "$options": "i"}}
    if exclude_owner_id:
        query["owner_id"] = {"$ne": exclude_owner_id}

    existing = team_profiles.find_one(query)
    if existing:
        return True, existing.get("team_name")
    return False, None


def cleanup_expired_profiles():
    """
    Bulk delete all profiles that have passed their 30-day expiration.
    Called during nightly cleanup.
    Returns the number of profiles deleted.
    """
    now = datetime.datetime.utcnow().isoformat()
    result = team_profiles.delete_many({
        "expires_at": {"$exists": True, "$lt": now}
    })
    return result.deleted_count
