"""
Mack Bot — Global Teams Model
Tracks team identity, stats, and tier placement across all scrims and days.
Used for global leaderboards, promotion/demotion, and cross-tier tracking.
"""

import datetime
from database import global_teams


# ═══════════════════ CRUD ═══════════════════


def upsert_team(owner_id: str, team_name: str, current_tier: str = "T3"):
    """
    Create or update a global team record.
    Uses owner_id as the unique identifier.
    """
    team_key = team_name.strip().lower()
    now = datetime.datetime.utcnow().isoformat()

    global_teams.update_one(
        {"owner_id": owner_id},
        {
            "$setOnInsert": {
                "owner_id": owner_id,
                "team_key": team_key,
                "created_at": now,
                "promoted_at": None,
                "demoted_at": None,
            },
            "$set": {
                "team_name": team_name.strip(),
                "current_tier": current_tier.upper(),
                "updated_at": now,
            },
            "$setOnInsert": {
                "total_points": 0,
                "matches_played": 0,
                "total_kills": 0,
                "wins": 0,
                "no_shows": 0,
            }
        },
        upsert=True
    )


def get_team(owner_id: str):
    """Get a global team by owner ID."""
    return global_teams.find_one({"owner_id": owner_id})


def get_team_by_name(team_name: str):
    """Get a global team by team name (case-insensitive)."""
    return global_teams.find_one({"team_key": team_name.strip().lower()})


def get_leaderboard(tier: str = None, limit: int = 25):
    """
    Get the global leaderboard, optionally filtered by tier.
    Sorted by total_points descending.
    """
    query = {}
    if tier:
        query["current_tier"] = tier.upper()
    return list(global_teams.find(query).sort("total_points", -1).limit(limit))


def update_team_stats(owner_id: str, points: int = 0, kills: int = 0,
                      matches: int = 0, wins: int = 0):
    """
    Increment a team's global stats after a match result is recorded.
    """
    global_teams.update_one(
        {"owner_id": owner_id},
        {
            "$inc": {
                "total_points": points,
                "total_kills": kills,
                "matches_played": matches,
                "wins": wins,
            },
            "$set": {"updated_at": datetime.datetime.utcnow().isoformat()}
        }
    )


def increment_no_shows(owner_id: str):
    """Increment the no-show counter for a team."""
    global_teams.update_one(
        {"owner_id": owner_id},
        {
            "$inc": {"no_shows": 1},
            "$set": {"updated_at": datetime.datetime.utcnow().isoformat()}
        }
    )


def promote_team(owner_id: str, new_tier: str):
    """Promote a team to a higher tier."""
    now = datetime.datetime.utcnow().isoformat()
    global_teams.update_one(
        {"owner_id": owner_id},
        {"$set": {
            "current_tier": new_tier.upper(),
            "promoted_at": now,
            "updated_at": now,
        }}
    )


def demote_team(owner_id: str, new_tier: str):
    """Demote a team to a lower tier."""
    now = datetime.datetime.utcnow().isoformat()
    global_teams.update_one(
        {"owner_id": owner_id},
        {"$set": {
            "current_tier": new_tier.upper(),
            "demoted_at": now,
            "updated_at": now,
        }}
    )


def get_promotion_candidates(tier: str, limit: int = 5):
    """Get top teams from a tier eligible for promotion (sorted by total_points desc)."""
    return list(global_teams.find(
        {"current_tier": tier.upper()}
    ).sort("total_points", -1).limit(limit))


def get_demotion_candidates(tier: str, limit: int = 5):
    """Get bottom teams from a tier eligible for demotion (sorted by total_points asc)."""
    return list(global_teams.find(
        {"current_tier": tier.upper()}
    ).sort("total_points", 1).limit(limit))


def get_all_teams_in_tier(tier: str):
    """Get all teams in a specific tier."""
    return list(global_teams.find(
        {"current_tier": tier.upper()}
    ).sort("total_points", -1))
