"""
Mack Bot Tortuga — Team Profile Model
CRUD operations for saved team profiles.
Teams persist across days so returning players can reuse them.
"""

import datetime
from database import team_profiles


def get_profile(owner_id: str):
    """Get a saved team profile by Discord user ID."""
    return team_profiles.find_one({"owner_id": owner_id})


def save_profile(owner_id: str, team_name: str, players: list, teammate_ids: list = None):
    """
    Create or update a team profile.
    
    Args:
        owner_id: Discord user ID (as string)
        team_name: Team display name
        players: List of player in-game names
        teammate_ids: List of Discord user IDs of teammates
    """
    team_profiles.update_one(
        {"owner_id": owner_id},
        {"$set": {
            "owner_id": owner_id,
            "team_name": team_name,
            "players": players,
            "teammate_ids": teammate_ids or [],
            "updated_at": datetime.datetime.utcnow().isoformat()
        }},
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
