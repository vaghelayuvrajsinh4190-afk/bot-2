"""
Mack Bot Tortuga — Help Menu Cog
Interactive help menu with dropdown navigation.
"""

import discord
from discord.ext import commands
from discord import app_commands, ui

from config import Theme
from utils.embeds import make_embed


class HelpDropdown(ui.Select):
    """Dropdown for navigating help categories."""
    
    def __init__(self, is_admin):
        options = [
            discord.SelectOption(label="Overview", description="Bot info & quick start", emoji="🏠", value="overview", default=True),
            discord.SelectOption(label="Registration", description="How to register for scrims", emoji="📝", value="register"),
        ]
        if is_admin:
            options.extend([
                discord.SelectOption(label="Provisioning", description="Daily group setup", emoji="⚙️", value="provision"),
                discord.SelectOption(label="Admin Panel", description="Manage matches, bans, reminders", emoji="🔧", value="panel"),
                discord.SelectOption(label="Announcements", description="Announce, room, DM broadcast", emoji="📢", value="announce"),
                discord.SelectOption(label="Configuration", description="Bot settings & channels", emoji="🔧", value="config"),
            ])
        super().__init__(placeholder="📖 Select a category…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        pages = {
            "overview": self._overview,
            "register": self._register,
            "provision": self._provision,
            "panel": self._panel,
            "announce": self._announce,
            "config": self._config,
        }
        embed = pages.get(self.values[0], self._overview)()
        await interaction.response.edit_message(embed=embed)

    def _overview(self):
        return make_embed(
            "⚡ Mack Bot Tortuga — Command Center",
            f"{Theme.SEP}\n\n"
            f"Welcome to **Mack Bot Tortuga** — your tournament & scrim manager!\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**🚀 How It Works:**\n"
            f"  `1.` Admin runs `/provision` to set up today's groups\n"
            f"  `2.` Players click **Register** or use `/register`\n"
            f"  `3.` Fill in team details & select squad members\n"
            f"  `4.` Auto-assigned to the next available group\n"
            f"  `5.` Get match reminders, room credentials in your group channel\n"
            f"  `6.` Play and submit screenshots!\n\n"
            f"**Key Differences from Old Bot:**\n"
            f"  ◆ Dynamic daily groups (no fixed matches)\n"
            f"  ◆ Atomic slot claiming (no race conditions)\n"
            f"  ◆ Auto channel/role creation & cleanup\n"
            f"  ◆ Cancel/reschedule with time locks\n\n{Theme.SEP}",
            Theme.PREMIUM, "📖 Overview"
        )

    def _register(self):
        return make_embed(
            "📝 Registration Guide",
            f"{Theme.SEP}\n\n"
            f"**Player Commands:**\n\n"
            f"**`/register`** — Register for today's scrims\n"
            f"╰ Opens team form → select teammates → auto-assigned to group\n\n"
            f"**`/myteam`** — View your team profile & today's registration\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**In Your Group Channel:**\n"
            f"  🚪 **Cancel Slot** — Leave your group (blocked <20 min before match)\n"
            f"  🔄 **Change Schedule** — Move to a different group\n\n"
            f"**Saved Profiles:**\n"
            f"  Your team info is saved! Next time you register, you can reuse it.\n\n{Theme.SEP}",
            Theme.TEAL, "📖 Registration"
        )

    def _provision(self):
        return make_embed(
            "⚙️ Provisioning Guide",
            f"{Theme.SEP}\n\n"
            f"**`/provision`** — Create today's groups\n"
            f"╰ Creates category, channels, roles, and group DB docs\n"
            f"╰ Options: group_count, capacity, match times, maps, stagger\n\n"
            f"**`/addgroups`** — Add more groups if current ones fill up\n\n"
            f"**`/deprovision`** — Remove today's groups (channels, roles, archive data)\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**Automatic:**\n"
            f"  🕛 Nightly cleanup runs at midnight IST\n"
            f"  ◆ Deletes yesterday's channels & roles\n"
            f"  ◆ Archives data (preserved for history)\n"
            f"  ◆ Cleans up expired bans\n\n{Theme.SEP}",
            Theme.ROSE, "📖 Provisioning"
        )

    def _panel(self):
        return make_embed(
            "🔧 Admin Panel Guide",
            f"{Theme.SEP}\n\n"
            f"**`/panel`** — Open the admin control panel\n\n"
            f"**Panel Buttons:**\n"
            f"  🔔 **Match Reminder** — Send reminder to a group\n"
            f"  📋 **Publish Slot List** — Post frozen roster\n"
            f"  🔧 **Manage Matches** → Edit match details or move teams\n"
            f"  🔨 **Punish Team** — Ban a player\n"
            f"  🏆 **Qualified Teams** — View standings\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**Other Admin Commands:**\n"
            f"  `/remind G0001` — Send reminder to a specific group\n"
            f"  `/lockgroup G0001` — Lock and publish slot list\n"
            f"  `/slotlist G0001` — Publish slot list without locking\n"
            f"  `/unban @user` — Remove a ban\n"
            f"  `/banlist` — View active bans\n\n{Theme.SEP}",
            Theme.ACCENT, "📖 Admin Panel"
        )

    def _announce(self):
        return make_embed(
            "📢 Announcements Guide",
            f"{Theme.SEP}\n\n"
            f"**`/announce #channel message`** — Post announcement\n\n"
            f"**`/room G0001 roomid password`** — Send room credentials to a group\n\n"
            f"**`/dm @user1 @user2 message`** — DM specific members\n\n"
            f"**`/dmall message`** — Broadcast to all server members\n\n"
            f"**`/clear [amount]`** — Purge messages in current channel\n\n{Theme.SEP}",
            Theme.ORANGE, "📖 Announcements"
        )

    def _config(self):
        return make_embed(
            "🔧 Configuration Guide",
            f"{Theme.SEP}\n\n"
            f"**`/config setting #channel`** — Set a channel\n\n"
            f"**Channel Settings:**\n"
            f"  ◆ `register_channel` — Where the register button lives\n"
            f"  ◆ `admin_channel` — For admin commands\n"
            f"  ◆ `admin_log_channel` — Bot action logs\n"
            f"  ◆ `leaderboard_channel` — Leaderboard posting\n\n"
            f"**Number Settings:**\n"
            f"  ◆ `default_group_count` — Groups per provision (default 10)\n"
            f"  ◆ `default_group_capacity` — Teams per group (default 21)\n"
            f"  ◆ `reminder_lead_minutes` — Reminder before match (default 30)\n"
            f"  ◆ `lock_minutes` — Lock cancel/reschedule before match (default 20)\n\n"
            f"**`/viewconfig`** — See all current settings\n\n{Theme.SEP}",
            Theme.PREMIUM, "📖 Configuration"
        )


class HelpView(ui.View):
    def __init__(self, is_admin):
        super().__init__(timeout=180)
        self.add_item(HelpDropdown(is_admin))


class HelpCog(commands.Cog):
    """Interactive help menu."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show the interactive help menu")
    async def help_cmd(self, interaction: discord.Interaction):
        is_admin = interaction.user.guild_permissions.administrator
        dropdown = HelpDropdown(is_admin)
        embed = dropdown._overview()
        await interaction.response.send_message(embed=embed, view=HelpView(is_admin), ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
