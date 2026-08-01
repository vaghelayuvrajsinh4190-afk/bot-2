"""
Mack Bot — Configuration & Constants
All environment variables, theme colors, and bot-wide settings.
"""

import os
import json
import discord
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════ ENVIRONMENT ═══════════════════

TOKEN = os.environ.get("TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
OWNER_ID = os.environ.get("OWNER_ID")
GUILD_ID = os.environ.get("GUILD_ID")

# ═══════════════════ BOT SETTINGS ═══════════════════

BOT_PREFIX = "!"
TIMEZONE_OFFSET = 5.5  # IST = UTC+5:30

# Default group settings (admin can override via /config)
DEFAULT_GROUP_CAPACITY = 21
DEFAULT_GROUP_COUNT = 12  # Blueprint: 12 groups
DEFAULT_RESERVED_SLOTS = 1   # Slot 01 reserved by default
DEFAULT_REMINDER_LEAD_MINUTES = 30
DEFAULT_LOCK_MINUTES = 20  # lock cancel/reschedule this many min before match

# Registration timing (IST)
REGISTRATION_OPEN_HOUR = 10   # 10:00 AM IST
REGISTRATION_OPEN_MINUTE = 0

# Default category name for provisioned groups
DEFAULT_CATEGORY_NAME = "📋 SCRIMS"

# Team profile expiry
PROFILE_EXPIRY_DAYS = 30  # 30-day memory for team profiles

# Default scrim ID (backward compatibility)
DEFAULT_SCRIM_ID = "SQ"


def get_today_event_id(scrim_id: str = None) -> str:
    """
    Get today's event ID based on IST date.
    If scrim_id is provided, returns SCRIMID_YYYY-MM-DD.
    Otherwise returns just YYYY-MM-DD (legacy fallback).
    """
    import datetime
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    date_str = local_now.strftime("%Y-%m-%d")
    
    if scrim_id:
        return f"{scrim_id.upper()}_{date_str}"
    return date_str

# ═══════════════════ SCHEDULE LOADER ═══════════════════

SCHEDULE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.json")


def load_schedule(scrim_id: str = None):
    """
    Load the daily schedule.
    If scrim_id is provided, loads from the scrim's own schedule in MongoDB,
    then falls back to schedule.json (per-scrim nested format).
    Otherwise falls back to global MongoDB config, then schedule.json.
    """
    # Try per-scrim schedule from MongoDB first
    if scrim_id:
        try:
            from models.scrim import get_scrim_schedule
            scrim_schedule = get_scrim_schedule(scrim_id)
            if scrim_schedule:
                return scrim_schedule
        except Exception as e:
            print(f"⚠️ Failed to load schedule for scrim {scrim_id}: {e}", flush=True)

    # Try global MongoDB config
    try:
        from database import get_config
        db_schedule = get_config("schedule")
        if db_schedule is not None:
            return db_schedule
    except Exception as e:
        print(f"⚠️ Failed to load schedule from MongoDB: {e}", flush=True)

    # Fallback: read from schedule.json (per-scrim nested format)
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # New per-scrim nested format: {"SQ": {"groups": [...]}, "T3": {"groups": [...]}}
        if scrim_id and scrim_id.upper() in data:
            groups_data = data[scrim_id.upper()].get("groups", [])
        elif "groups" in data:
            # Legacy flat format: {"groups": [...]}
            groups_data = data.get("groups", [])
        else:
            # Default: try SQ key as fallback
            groups_data = data.get("SQ", {}).get("groups", [])

        # Auto-migrate to MongoDB for persistence
        try:
            from database import set_config
            set_config("schedule", groups_data)
            print("✅ Migrated schedule.json to MongoDB.", flush=True)
        except Exception:
            pass
        return groups_data
    except FileNotFoundError:
        print("⚠️ schedule.json not found and no schedule in MongoDB, using empty schedule.", flush=True)
        return []
    except json.JSONDecodeError as e:
        print(f"⚠️ schedule.json parse error: {e}", flush=True)
        return []


def save_schedule(groups_data, scrim_id: str = None):
    """
    Save updated schedule data.
    If scrim_id is provided, saves to the scrim's own schedule in MongoDB
    and updates the per-scrim key in schedule.json.
    Otherwise saves to global MongoDB config.
    """
    if scrim_id:
        try:
            from models.scrim import set_scrim_schedule
            set_scrim_schedule(scrim_id, groups_data)
        except Exception as e:
            print(f"❌ Failed to save schedule for scrim {scrim_id}: {e}", flush=True)
            return False

        # Also update schedule.json with per-scrim nested format
        try:
            existing = {}
            try:
                with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            existing[scrim_id.upper()] = {"groups": groups_data}
            with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # Non-fatal
        return True

    try:
        from database import set_config
        set_config("schedule", groups_data)
    except Exception as e:
        print(f"❌ Failed to save schedule to MongoDB: {e}", flush=True)
        return False

    # Also write to local file as backup (best-effort, per-scrim format)
    try:
        existing = {}
        try:
            with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # Store under "SQ" key as default when no scrim_id specified
        existing["SQ"] = {"groups": groups_data}
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    except Exception:
        pass  # Non-fatal — MongoDB is the source of truth now

    return True


def get_schedule_for_group(group_number: int, scrim_id: str = None):
    """
    Get the schedule entry for a specific group number (1-based).
    If scrim_id is provided, loads that scrim's schedule.
    Returns dict with match1/match2 or None if not found.
    """
    schedule = load_schedule(scrim_id)
    for entry in schedule:
        if entry.get("group_number") == group_number:
            return entry
    return None


# ═══════════════════ DESIGN SYSTEM ═══════════════════

class Theme:
    """Centralized color palette and visual constants for embeds."""
    SUCCESS   = discord.Color.from_rgb(46, 252, 103)  # electric neon green
    ERROR     = discord.Color.from_rgb(255, 59, 48)   # electric red
    WARNING   = discord.Color.from_rgb(255, 204, 0)   # electric gold
    INFO      = discord.Color.from_rgb(0, 122, 255)   # electric blue
    PREMIUM   = discord.Color.from_rgb(191, 90, 242)  # cyber purple
    ACCENT    = discord.Color.from_rgb(0, 255, 213)   # cyan
    DARK      = discord.Color.from_rgb(24, 25, 28)    # cyber dark
    TEAL      = discord.Color.from_rgb(48, 209, 88)   # neon teal
    ORANGE    = discord.Color.from_rgb(255, 159, 10)  # electric orange
    ROSE      = discord.Color.from_rgb(255, 55, 127)  # electric rose
    GOLD      = discord.Color.from_rgb(255, 215, 0)   # pure gold
    CRIMSON   = discord.Color.from_rgb(255, 69, 58)   # crimson

    # Visual separators
    SEP       = "✦ ─────────────────── ✦"
    THIN_SEP  = "────────────────────────"
    FOOTER    = "✦ Mack Bot 🚀 2027 Edition"
    BULLET    = "✦"
    ARROW     = "›"

    @staticmethod
    def bar(current, maximum, length=10):
        """Generate a progress bar string using solid blocks."""
        filled = int((current / maximum) * length) if maximum else 0
        return "`" + "█" * filled + "░" * (length - filled) + "`"

    # Alias slot_bar to bar to prevent duplication
    slot_bar = bar

    @staticmethod
    def group_color(count, mx):
        """Color based on fill ratio."""
        r = count / mx if mx else 0
        if r >= 1.0: return Theme.ERROR
        if r >= 0.75: return Theme.ORANGE
        if r >= 0.4: return Theme.WARNING
        return Theme.SUCCESS

    @staticmethod
    def group_status(count, mx):
        """Status text based on fill ratio."""
        r = count / mx if mx else 0
        if r >= 1.0: return "⚡ FULL / LOCKED"
        if r >= 0.75: return "▲ Almost Full"
        if r >= 0.4: return "✦ Filling Up"
        return "🟢 Active / Open"

# ═══════════════════ RANK EMOJIS ═══════════════════

RANK_EMOJIS = {
    1: "🥇", 2: "🥈", 3: "🥉",
    4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣",
    9: "9️⃣", 10: "🔟"
}

def get_rank_emoji(rank):
    return RANK_EMOJIS.get(rank, f"`{rank}.`")

# ═══════════════════ DEFAULT POINTS ═══════════════════

DEFAULT_POSITION_POINTS = {
    "1": 15, "2": 12, "3": 10, "4": 8, "5": 6,
    "6": 4, "7": 2, "8": 1, "9": 0, "10": 0,
    "11": 0, "12": 0, "13": 0, "14": 0, "15": 0, "16": 0
}


# ═══════════════════ SCRIM CONFIG LOADERS ═══════════════════

def get_effective_scrim_config(scrim_id: str = None, key: str = None, default=None):
    """
    Get a setting value for a specific scrim or global fallback.
    If scrim_id is provided and not 'Global', checks scrim settings first.
    """
    if scrim_id and scrim_id.upper() != "GLOBAL":
        try:
            from models.scrim import get_scrim_setting
            val = get_scrim_setting(scrim_id.upper(), key)
            if val is not None:
                return val
        except Exception:
            pass

    try:
        from database import get_config
        val = get_config(key)
        if val is not None:
            return val
    except Exception:
        pass

    return default


def get_effective_channel(scrim_id: str = None, channel_type: str = None):
    """
    Get channel ID for a specific scrim or global fallback.
    """
    if scrim_id and scrim_id.upper() != "GLOBAL":
        try:
            from models.scrim import get_scrim_channel
            ch_id = get_scrim_channel(scrim_id.upper(), channel_type)
            if ch_id is not None:
                return ch_id
        except Exception:
            pass

    try:
        from database import get_channel_config
        return get_channel_config(channel_type)
    except Exception:
        return None

