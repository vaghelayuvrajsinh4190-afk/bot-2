"""
Mack Bot — Help Menu Cog
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
                discord.SelectOption(label="Setup & Provisioning", description="Initial setup & daily groups", emoji="⚙️", value="provision"),
                discord.SelectOption(label="Admin Panel", description="Manage matches, bans, reminders", emoji="🔧", value="panel"),
                discord.SelectOption(label="Autopilot", description="Midnight reset & registration timers", emoji="🤖", value="autopilot"),
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
            "autopilot": self._autopilot,
            "announce": self._announce,
            "config": self._config,
        }
        embed = pages.get(self.values[0], self._overview)()
        await interaction.response.edit_message(embed=embed)

    def _overview(self):
        return make_embed(
            "⚡ Mack Bot — Command Center",
            f"{Theme.SEP}\n\n"
            f"Welcome to **Mack Bot** — your fully autonomous scrim manager!\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**🚀 How It Works:**\n"
            f"  `1.` Bot auto-creates groups at midnight using `schedule.json`\n"
            f"  `2.` Registration opens at **10:00 AM IST**\n"
            f"  `3.` Players click **📥 Register Team** in `#register-here`\n"
            f"  `4.` Fill in team details (3-step form) & select squad\n"
            f"  `5.` Auto-assigned to the lowest available group\n"
            f"  `6.` Get match reminders + room credentials in your group channel\n"
            f"  `7.` Play and submit screenshots!\n\n"
            f"**🔑 Key Features:**\n"
            f"  ◆ **12 daily groups** (Day 1-6 + Evening 7-12)\n"
            f"  ◆ **30-day team memory** — reuse your saved profile\n"
            f"  ◆ **Atomic slot claiming** — no race conditions\n"
            f"  ◆ **Auto channel/role cleanup** at midnight\n"
            f"  ◆ **Anti-crash system** — bot never goes offline\n\n{Theme.SEP}",
            Theme.PREMIUM, "📖 Overview"
        )

    def _register(self):
        return make_embed(
            "📝 Registration Guide",
            f"{Theme.SEP}\n\n"
            f"**How to Register:**\n\n"
            f"  `1.` Go to `#register-here`\n"
            f"  `2.` Click **📥 Register Team**\n"
            f"  `3.` **Step 1/3:** Enter Team Name, Owner Name, Email, Contact\n"
            f"  `4.` **Step 2/3:** Enter Player UIDs and IGNs\n"
            f"  `5.` **Step 3/3:** Select 4-5 squad members from dropdown\n"
            f"  `6.` Click **Confirm** → Auto-assigned to a group!\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**Returning Players (30-Day Memory):**\n"
            f"  📂 **Use Old Team** — Skip to teammate selection\n"
            f"  ✏️ **Edit Team** — Modify your saved profile\n"
            f"  ✨ **New Team** — Start fresh\n\n"
            f"**In Your Group Channel:**\n"
            f"  🛠️ **Manage Matches** → Cancel Slot or Change Group\n\n"
            f"**Commands:**\n"
            f"  `/register` — Alternative to the button\n"
            f"  `/myteam` — View your team profile & today's registration\n\n{Theme.SEP}",
            Theme.TEAL, "📖 Registration"
        )

    def _provision(self):
        return make_embed(
            "⚙️ Setup & Provisioning Guide",
            f"{Theme.SEP}\n\n"
            f"**First-Time Setup:**\n"
            f"  `/setup #register-here` — Deploy permanent registration board\n"
            f"  `/config register #register-here` — Set registration channel\n"
            f"  `/config admin_log #admin-log` — Set admin log channel\n"
            f"  `/config registered_teams #registered-teams` — Set receipt channel\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**Daily Provisioning:**\n"
            f"  `/provision` — Manually create today's groups (uses `schedule.json`)\n"
            f"  `/addgroups count:5` — Add more groups if current ones fill up\n"
            f"  `/deprovision` — Remove today's groups\n\n"
            f"**Schedule Management:**\n"
            f"  `/set_groups amount:10` — Set how many groups to create tonight\n"
            f"  `/update_time group:3 match:1 start:'2:00 PM' map:ERANGEL` — Edit schedule\n\n"
            f"**Automatic (Autopilot):**\n"
            f"  🕛 Midnight — Auto-cleanup + auto-provision\n"
            f"  🕙 10:00 AM — Registration unlocks\n\n{Theme.SEP}",
            Theme.ROSE, "📖 Provisioning"
        )

    def _panel(self):
        return make_embed(
            "🔧 Admin Panel & Group Controls",
            f"{Theme.SEP}\n\n"
            f"**`/panel`** — Open the admin control panel\n\n"
            f"**Group Control Panel (in each group channel):**\n"
            f"  ⏰ **Match Reminder** *(Admin Only)*\n"
            f"  📤 **Publish Slot List** *(Admin Only)*\n"
            f"  🛠️ **Manage Matches** *(Teams & Admins)*\n"
            f"  🔨 **Punish Team** *(Admin Only)*\n"
            f"  🌟 **Qualified Teams** *(Admin Only)*\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**Other Admin Commands:**\n"
            f"  `/remind G0001` — Send reminder to a specific group\n"
            f"  `/lockgroup G0001` — Lock and publish slot list\n"
            f"  `/slotlist G0001` — Publish slot list without locking\n"
            f"  `/unban @user` — Remove a ban\n"
            f"  `/banlist` — View active bans\n\n{Theme.SEP}",
            Theme.ACCENT, "📖 Admin Panel"
        )

    def _autopilot(self):
        return make_embed(
            "🤖 Autopilot System",
            f"{Theme.SEP}\n\n"
            f"The bot runs **24/7** with zero admin input needed.\n\n"
            f"**🕛 Midnight Reset (00:00 IST):**\n"
            f"  `1.` Deletes yesterday's group channels\n"
            f"  `2.` Clears daily registration data\n"
            f"  `3.` Resets the registration board to 0/21\n"
            f"  `4.` Locks registration (🔒 button)\n"
            f"  `5.` Creates fresh group channels from `schedule.json`\n"
            f"  `6.` Deploys Control Panel in each group\n"
            f"  `7.` Cleans expired bans & 30-day profiles\n\n"
            f"**🕙 Registration Open (10:00 AM IST):**\n"
            f"  Unlocks the 📥 Register Team button\n\n"
            f"**🔒 Auto-Lock (T-20min before match):**\n"
            f"  Cancel/reschedule disabled, frozen slot list published\n\n"
            f"**⏰ Auto-Reminders (T-30min before match):**\n"
            f"  Reminder sent to group channel with match details\n\n"
            f"**24/7 Uptime:**\n"
            f"  Express keep-alive server + UptimeRobot 5-min pings\n\n{Theme.SEP}",
            Theme.GOLD, "📖 Autopilot"
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
            f"  ◆ `leaderboard_channel` — Leaderboard posting\n"
            f"  ◆ `registered_teams_channel` — Public registration receipts\n\n"
            f"**Number Settings:**\n"
            f"  ◆ `default_group_count` — Groups per provision (default 12)\n"
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
