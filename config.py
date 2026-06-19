"""
Mack Bot Tortuga — Configuration & Constants
All environment variables, theme colors, and bot-wide settings.
"""

import os
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
DEFAULT_GROUP_COUNT = 10
DEFAULT_REMINDER_LEAD_MINUTES = 30
DEFAULT_LOCK_MINUTES = 20  # lock cancel/reschedule this many min before match

# ═══════════════════ DESIGN SYSTEM ═══════════════════

class Theme:
    """Centralized color palette and visual constants for embeds."""
    SUCCESS   = discord.Color.from_rgb(87, 242, 135)
    ERROR     = discord.Color.from_rgb(237, 66, 69)
    WARNING   = discord.Color.from_rgb(254, 231, 92)
    INFO      = discord.Color.from_rgb(88, 101, 242)
    PREMIUM   = discord.Color.from_rgb(167, 139, 250)
    ACCENT    = discord.Color.from_rgb(45, 136, 255)
    DARK      = discord.Color.from_rgb(43, 45, 49)
    TEAL      = discord.Color.from_rgb(30, 224, 188)
    ORANGE    = discord.Color.from_rgb(250, 168, 26)
    ROSE      = discord.Color.from_rgb(235, 69, 158)
    GOLD      = discord.Color.from_rgb(255, 215, 0)
    CRIMSON   = discord.Color.from_rgb(220, 20, 60)

    # Visual separators
    SEP       = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
    THIN_SEP  = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"
    FOOTER    = "⚡ Mack Bot Tortuga │ Powered by Precision"
    BULLET    = "╰"
    ARROW     = "➤"

    @staticmethod
    def bar(current, maximum, length=12):
        """Generate a progress bar string."""
        filled = int((current / maximum) * length) if maximum else 0
        return "`" + "█" * filled + "░" * (length - filled) + "`"

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
        if r >= 1.0: return "🔴 FULL"
        if r >= 0.75: return "🟠 Almost Full"
        if r >= 0.4: return "🟡 Filling Up"
        return "🟢 Open"

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
