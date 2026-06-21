"""
Mack Bot — Punishment Model
CRUD operations for bans, strikes, and punishments.
"""

import datetime
from database import punishments


def ban_user(owner_id: str, username: str, reason: str, days: int, banned_by: str):
    """
    Ban a user. days=0 means permanent.
    
    Args:
        owner_id: Discord user ID
        username: Display name for logging
        reason: Ban reason
        days: Duration in days (0 = permanent)
        banned_by: Admin who issued the ban
    """
    if days == 0:
        expires_at = "never"
    else:
        expires_at = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()

    punishments.update_one(
        {"owner_id": owner_id, "type": "ban", "active": True},
        {"$set": {
            "owner_id": owner_id,
            "username": username,
            "type": "ban",
            "reason": reason,
            "days": days,
            "expires_at": expires_at,
            "banned_by": banned_by,
            "banned_at": datetime.datetime.utcnow().isoformat(),
            "active": True
        }},
        upsert=True
    )


def unban_user(owner_id: str):
    """Remove an active ban."""
    result = punishments.update_one(
        {"owner_id": owner_id, "type": "ban", "active": True},
        {"$set": {"active": False, "unbanned_at": datetime.datetime.utcnow().isoformat()}}
    )
    return result.modified_count > 0


def is_banned(owner_id: str):
    """
    Check if a user is currently banned.
    Also handles automatic expiry.
    Returns (is_banned, ban_doc or None).
    """
    ban = punishments.find_one({
        "owner_id": owner_id,
        "type": "ban",
        "active": True
    })

    if not ban:
        return False, None

    expires_at = ban.get("expires_at")
    if expires_at == "never":
        return True, ban

    try:
        exp_dt = datetime.datetime.fromisoformat(expires_at)
        if datetime.datetime.utcnow() > exp_dt:
            # Ban expired — deactivate it
            punishments.update_one(
                {"_id": ban["_id"]},
                {"$set": {"active": False, "expired": True}}
            )
            return False, None
    except Exception:
        pass

    return True, ban


def get_active_bans():
    """Get all currently active bans."""
    bans = list(punishments.find({"type": "ban", "active": True}))
    
    # Clean up expired bans while we're at it
    now = datetime.datetime.utcnow()
    active = []
    for ban in bans:
        exp = ban.get("expires_at")
        if exp == "never":
            active.append(ban)
            continue
        try:
            exp_dt = datetime.datetime.fromisoformat(exp)
            if now > exp_dt:
                punishments.update_one(
                    {"_id": ban["_id"]},
                    {"$set": {"active": False, "expired": True}}
                )
            else:
                active.append(ban)
        except Exception:
            active.append(ban)
    
    return active


def cleanup_expired_bans():
    """Bulk expire all old bans. Called during nightly cleanup."""
    now = datetime.datetime.utcnow().isoformat()
    result = punishments.update_many(
        {
            "type": "ban",
            "active": True,
            "expires_at": {"$ne": "never", "$lt": now}
        },
        {"$set": {"active": False, "expired": True}}
    )
    return result.modified_count
