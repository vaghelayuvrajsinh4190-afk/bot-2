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
DEFAULT_REMINDER_LEAD_MINUTES = 30
DEFAULT_LOCK_MINUTES = 20  # lock cancel/reschedule this many min before match

# Registration timing (IST)
REGISTRATION_OPEN_HOUR = 10   # 10:00 AM IST
REGISTRATION_OPEN_MINUTE = 0

# Team profile expiry
PROFILE_EXPIRY_DAYS = 30  # 30-day memory for team profiles

# ═══════════════════ SCHEDULE LOADER ═══════════════════

SCHEDULE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.json")


def load_schedule():
    """Load the daily schedule from schedule.json."""
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("groups", [])
    except FileNotFoundError:
        print("⚠️ schedule.json not found, using empty schedule.", flush=True)
        return []
    except json.JSONDecodeError as e:
        print(f"⚠️ schedule.json parse error: {e}", flush=True)
        return []


def save_schedule(groups_data):
    """Save updated schedule data back to schedule.json."""
    try:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump({"groups": groups_data}, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ Failed to save schedule.json: {e}", flush=True)
        return False


def get_schedule_for_group(group_number: int):
    """
    Get the schedule entry for a specific group number (1-based).
    Returns dict with match1/match2 or None if not found.
    """
    schedule = load_schedule()
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
    def bar(current, maximum, length=12):
        """Generate a progress bar string using parallelograms."""
        filled = int((current / maximum) * length) if maximum else 0
        return "`" + "▰" * filled + "▱" * (length - filled) + "`"

    @staticmethod
    def slot_bar(current, maximum, length=10):
        """Generate a progress bar using circle style (blueprint style)."""
        filled = int((current / maximum) * length) if maximum else 0
        return "●" * filled + "○" * (length - filled)

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
