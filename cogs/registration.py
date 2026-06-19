"""
Mack Bot Tortuga — Registration Cog
Handles the /register flow: saved profiles, team modal, teammate selection, atomic slot claim.
Covers Phases 1, 2, and 3 of the upgrade plan.
"""

import datetime
import discord
from discord.ext import commands
from discord import app_commands, ui

from config import Theme, TIMEZONE_OFFSET
from utils.embeds import make_embed, error_embed, success_embed, build_roster_embed
from models import team_profile, group as group_model, registration as reg_model, punishment


# ═══════════════════ HELPERS ═══════════════════

def get_today_event_id():
    """Get today's event ID based on IST date."""
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")


# ═══════════════════ MODALS ═══════════════════

class TeamRegistrationModal(ui.Modal, title="📋 Team Registration"):
    """Modal for entering team details."""
    
    team_name = ui.TextInput(
        label="Team Name",
        placeholder="Enter a unique team name (e.g. Galaxy Crows)",
        max_length=50,
        style=discord.TextStyle.short
    )
    
    players = ui.TextInput(
        label="Player Names (one per line)",
        placeholder="Player1 (IGL)\nPlayer2\nPlayer3\nPlayer4",
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        owner_id = str(interaction.user.id)
        name = self.team_name.value.strip()
        player_list = [p.strip() for p in self.players.value.strip().split("\n") if p.strip()]

        if not name:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Team Name", "Team name cannot be empty."),
                ephemeral=True
            )
            return

        if len(player_list) < 1:
            await interaction.response.send_message(
                embed=error_embed("❌ No Players", "You must enter at least one player name."),
                ephemeral=True
            )
            return

        # Check for duplicate player names within the form
        clean_players = [p.lower() for p in player_list]
        if len(clean_players) != len(set(clean_players)):
            await interaction.response.send_message(
                embed=error_embed("❌ Duplicate Players", "You entered the same player name twice."),
                ephemeral=True
            )
            return

        # Check team name uniqueness
        is_dup, existing_owner = team_profile.check_duplicate_team_name(name, exclude_owner_id=owner_id)
        if is_dup:
            await interaction.response.send_message(
                embed=error_embed("❌ Name Taken", f"Team name **{name}** is already registered by another squad!"),
                ephemeral=True
            )
            return

        # Check player name uniqueness
        for p in player_list:
            is_dup, existing_team = team_profile.check_duplicate_player(p, exclude_owner_id=owner_id)
            if is_dup:
                await interaction.response.send_message(
                    embed=error_embed("❌ Player Taken", f"Player **{p}** is already in team **{existing_team}**!"),
                    ephemeral=True
                )
                return

        # Save the team profile
        team_profile.save_profile(owner_id, name, player_list)

        # Show teammate selection
        roster_text = "\n".join([f"  │  ✦ {p}" for p in player_list])
        embed = make_embed(
            "👥 Final Step: Select Teammates",
            f"✅ Team **{name}** profile saved!\n\n"
            f"╭── 📋 **Roster** ──╮\n{roster_text}\n╰───────────────────╯\n\n"
            f"{Theme.THIN_SEP}\n"
            f"**Next:** Select your **4 to 5 squad members** from the dropdown below.\n"
            f"You must include yourself.",
            Theme.ACCENT,
            "Step 2 of 2 — Select teammates"
        )
        await interaction.response.send_message(
            embed=embed,
            view=TeammateSelectView(name, player_list),
            ephemeral=True
        )


# ═══════════════════ TEAMMATE SELECTION ═══════════════════

class ConfirmRegistrationView(ui.View):
    """View with a button to finalize registration."""
    
    def __init__(self, team_name, players, selected_members):
        super().__init__(timeout=120)
        self.team_name = team_name
        self.players = players
        self.selected_members = selected_members

    @ui.button(
        label="Confirm & Complete Registration",
        style=discord.ButtonStyle.primary,
        emoji="✅",
        custom_id="finalize_reg_btn"
    )
    async def confirm_registration(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()
        
        # Check if banned
        is_ban, ban_doc = punishment.is_banned(owner_id)
        if is_ban:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "You are banned and cannot register."),
                ephemeral=True
            )
            return

        # Check if already registered today
        if reg_model.is_already_registered(owner_id, event_id):
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "You are already registered for today."),
                ephemeral=True
            )
            return

        # Check teammate status
        for m in self.selected_members:
            is_reg, existing_team = reg_model.is_teammate_registered(str(m.id), event_id)
            if is_reg:
                await interaction.response.send_message(
                    embed=error_embed("❌ Error", f"{m.mention} is already registered in team **{existing_team}**."),
                    ephemeral=True
                )
                return

        # Check group availability
        available_groups = group_model.get_open_groups(event_id)
        if not available_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ All Groups Full", "All groups for today are completely full."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Atomic slot claim
        assigned_group = group_model.claim_slot(event_id)
        if not assigned_group:
            await interaction.followup.send(
                embed=error_embed("❌ Claim Failed", "All groups filled up while processing your request."),
                ephemeral=True
            )
            return

        # Create registration
        teammate_ids = [str(m.id) for m in self.selected_members]
        reg_model.create_registration(
            owner_id=owner_id,
            event_id=event_id,
            group_id=assigned_group["group_id"],
            team_name=self.team_name,
            players=self.players,
            teammate_ids=teammate_ids
        )

        # Save teammate IDs to profile
        team_profile.save_profile(owner_id, self.team_name, self.players, teammate_ids)

        # Grant group role
        guild = interaction.guild
        group_role = guild.get_role(assigned_group["role_id"])
        if group_role:
            for m in self.selected_members:
                try:
                    await m.add_roles(group_role)
                except (discord.Forbidden, Exception):
                    pass

        # Get match details
        m1 = assigned_group.get("match1", {})
        m2 = assigned_group.get("match2", {})
        group_id = assigned_group["group_id"]
        
        # Date display
        from cogs.provisioning import get_today_display
        event_date_display = get_today_display().title()

        # Build premium success embed matching the screenshot
        success = make_embed(
            "🎯 Registration Complete!",
            f"🏆 **Team** — `{self.team_name}`\n"
            f"⚙️ **Assigned Group** — `{group_id}`\n"
            f"📅 **Date** — `{event_date_display}`\n\n"
            f"╭── 🎮 **Match Schedule** ──╮\n"
            f"│  **Match 1:** `{m1.get('start', 'TBD')}` │ IDP `{m1.get('idp', 'TBD')}`\n"
            f"│  **Match 2:** `{m2.get('start', 'TBD')}` │ IDP `{m2.get('idp', 'TBD')}`\n"
            f"╰────────────────────────────╯\n\n"
            f"Please join your group channel when it's available.\n"
            f"🎱 **Best of luck 👊 for your matches!!**",
            Theme.SUCCESS,
            "Mack Bot Tortuga 🚀 2027 Edition"
        )
        await interaction.followup.send(embed=success, ephemeral=True)

        # Refresh the roster in the group channel
        await self._refresh_group_roster(interaction.guild, assigned_group, event_id)

        # Refresh the slot availability embed in register channel
        await self._refresh_slot_availability(interaction.guild, event_id)

    async def _refresh_group_roster(self, guild, group_doc, event_id):
        """Update the live roster embed in the group channel."""
        channel_id = group_doc.get("channel_id")
        if not channel_id:
            return
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return

        regs = reg_model.get_group_registrations(group_doc["group_id"], event_id)
        embed = build_roster_embed(group_doc, regs, group_doc["capacity"])

        msg_id = group_doc.get("roster_message_id")
        message = None
        if msg_id:
            try:
                message = await channel.fetch_message(msg_id)
                await message.edit(embed=embed)
            except discord.NotFound:
                message = None

        if message is None:
            message = await channel.send(embed=embed)
            group_model.update_roster_message(event_id, group_doc["group_id"], message.id)

    async def _refresh_slot_availability(self, guild, event_id):
        """Update the slot availability embed in the register channel."""
        from database import get_channel_config
        from utils.embeds import build_slot_availability_embed

        reg_channel_id = get_channel_config("register")
        if not reg_channel_id:
            return

        channel = guild.get_channel(reg_channel_id)
        if not channel:
            return

        all_groups = group_model.get_all_groups(event_id)
        embed = build_slot_availability_embed(all_groups)

        # Try to find and edit the existing availability message
        from database import get_config
        avail_msg_id = get_config(f"slot_availability_msg_{event_id}")
        if avail_msg_id:
            try:
                msg = await channel.fetch_message(avail_msg_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass

        # Post new one
        from database import set_config
        from cogs.registration import PersistentRegisterView
        msg = await channel.send(embed=embed, view=PersistentRegisterView())
        set_config(f"slot_availability_msg_{event_id}", msg.id)


class TeammateSelect(ui.UserSelect):
    """Dropdown to select 4 to 5 squad members."""
    
    def __init__(self, team_name, players):
        self.team_name = team_name
        self.players = players
        super().__init__(
            placeholder="Select your 4-5 teammates",
            min_values=4,
            max_values=5
        )

    async def callback(self, interaction: discord.Interaction):
        members = self.values
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        # Validation: must include yourself
        if interaction.user not in members:
            await interaction.response.send_message(
                embed=error_embed(
                    "⛔ Selection Error",
                    f"{Theme.SEP}\n\nYou must **include yourself** in the squad selection.\n"
                    f"*➤ Select yourself plus your teammates.*\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Validation: no bots
        bots = [m.mention for m in members if m.bot]
        if bots:
            await interaction.response.send_message(
                embed=error_embed(
                    "⛔ Selection Error",
                    f"{Theme.SEP}\n\nBots cannot be squad members:\n\n" +
                    "\n".join([f"  ⚠️ {b}" for b in bots]) +
                    f"\n\n*➤ Select only real players.*\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Validation: check if any teammate is already registered today
        for m in members:
            is_reg, existing_team = reg_model.is_teammate_registered(str(m.id), event_id)
            if is_reg:
                await interaction.response.send_message(
                    embed=error_embed(
                        "⛔ Already Registered",
                        f"{Theme.SEP}\n\n"
                        f"{m.mention} is already registered in team **{existing_team}** for today.\n"
                        f"Each player can only be in one team per day.\n\n{Theme.SEP}"
                    ),
                    ephemeral=True
                )
                return

        # Check if groups are available
        available_groups = group_model.get_open_groups(event_id)
        if not available_groups:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ All Groups Full",
                    f"{Theme.SEP}\n\n"
                    f"All groups for today are **completely full**!\n"
                    f"Please wait for an admin to add more groups.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Send confirmation button response
        confirm_view = ConfirmRegistrationView(self.team_name, self.players, members)
        await interaction.response.send_message(
            content="Teammates selected. Click the button below to Finalize Registration.",
            view=confirm_view,
            ephemeral=True
        )


class TeammateSelectView(ui.View):
    """View containing the teammate select dropdown."""
    
    def __init__(self, team_name, players):
        super().__init__(timeout=120)
        self.add_item(TeammateSelect(team_name, players))


# ═══════════════════ SAVED PROFILE VIEWS ═══════════════════

class SavedProfileView(ui.View):
    """Shown when a returning player has a saved team profile."""
    
    def __init__(self, profile):
        super().__init__(timeout=120)
        self.profile = profile

    @ui.button(label="Use Old Team", style=discord.ButtonStyle.secondary, emoji="📂", row=0)
    async def use_saved(self, interaction: discord.Interaction, button: ui.Button):
        team_name = self.profile["team_name"]
        players = self.profile.get("players", [])
        
        # Show teammate selection with saved data
        roster_text = "\n".join([f"  │  ✦ {p}" for p in players])
        embed = make_embed(
            "👥 Final Step: Select Teammates",
            f"Your team **{team_name}** has been loaded successfully!\n\n"
            f"╭── 📋 **Roster** ──╮\n{roster_text}\n╰───────────────────╯\n\n"
            f"{Theme.THIN_SEP}\n"
            f"**Next:** Select your **4 to 5 squad members** from the dropdown below.\n"
            f"You must include yourself.",
            Theme.ACCENT,
            "Step 2 of 2 — Select teammates"
        )
        await interaction.response.send_message(
            embed=embed,
            view=TeammateSelectView(team_name, players),
            ephemeral=True
        )

    @ui.button(label="Edit Team", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def edit_team(self, interaction: discord.Interaction, button: ui.Button):
        # Pre-fill modal with saved data
        modal = TeamRegistrationModal()
        modal.team_name.default = self.profile.get("team_name", "")
        modal.players.default = "\n".join(self.profile.get("players", []))
        await interaction.response.send_modal(modal)

    @ui.button(label="Register New Team", style=discord.ButtonStyle.secondary, emoji="🆕", row=1)
    async def new_team(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TeamRegistrationModal())


# ═══════════════════ PERSISTENT REGISTER BUTTON ═══════════════════

class PersistentRegisterView(ui.View):
    """The always-alive Register button posted in #register-here."""
    
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="📝 Register Your Squad",
        style=discord.ButtonStyle.green,
        custom_id="tortuga_register_btn"
    )
    async def register_button(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        # Check if banned
        is_ban, ban_doc = punishment.is_banned(owner_id)
        if is_ban:
            reason = ban_doc.get("reason", "No reason provided")
            exp = ban_doc.get("expires_at", "Unknown")
            if exp == "never":
                exp_display = "Permanent"
            else:
                try:
                    exp_dt = datetime.datetime.fromisoformat(exp) + datetime.timedelta(hours=TIMEZONE_OFFSET)
                    exp_display = exp_dt.strftime("%Y-%m-%d %I:%M %p")
                except Exception:
                    exp_display = exp

            await interaction.response.send_message(
                embed=make_embed(
                    "⛔ Access Denied — Banned",
                    f"You are currently **banned** from scrims.\n\n"
                    f"╭── 🔨 **Ban Details** ──╮\n"
                    f"│  📝 **Reason:** {reason}\n"
                    f"│  ⏳ **Expires:** `{exp_display}`\n"
                    f"╰────────────────────────╯\n\n"
                    f"*If you believe this is an error, contact an admin.*",
                    Theme.ERROR
                ),
                ephemeral=True
            )
            return

        # Check if already registered today
        existing = reg_model.get_registration(owner_id, event_id)
        if existing:
            group_id = existing.get("group_id", "???")
            team_name = existing.get("team_name", "???")
            await interaction.response.send_message(
                embed=make_embed(
                    "⚠️ Already Registered",
                    f"You're already registered for today!\n\n"
                    f"╭── 📋 **Your Registration** ──╮\n"
                    f"│  🏷️ **Team:** `{team_name}`\n"
                    f"│  📍 **Group:** `{group_id}`\n"
                    f"╰────────────────────────────╯\n\n"
                    f"*Use the Cancel/Change buttons in your group channel if needed.*",
                    Theme.WARNING
                ),
                ephemeral=True
            )
            return

        # Check if groups are provisioned
        available = group_model.get_open_groups(event_id)
        all_groups = group_model.get_all_groups(event_id)
        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ No Groups Available",
                    f"{Theme.SEP}\n\n"
                    f"Today's scrims haven't been set up yet.\n"
                    f"An admin needs to run the provisioning command first.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        if not available:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ All Groups Full",
                    f"{Theme.SEP}\n\n"
                    f"All groups for today are completely full!\n"
                    f"Please wait for an admin to add more groups.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Check for saved profile
        profile = team_profile.get_profile(owner_id)
        if profile:
            await interaction.response.send_message(
                content="You have a saved team profile:",
                view=SavedProfileView(profile),
                ephemeral=True
            )
        else:
            # New user — show modal
            await interaction.response.send_modal(TeamRegistrationModal())


# ═══════════════════ COG ═══════════════════

class RegistrationCog(commands.Cog):
    """Handles team registration flow."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Register persistent views on cog load."""
        self.bot.add_view(PersistentRegisterView())

    @app_commands.command(name="register", description="Register your team for today's scrims")
    async def register_slash(self, interaction: discord.Interaction):
        """Slash command alternative to the button."""
        # Reuse the same logic as the button
        view = PersistentRegisterView()
        await view.register_button.callback(interaction)


    @app_commands.command(name="myteam", description="View your team info and today's registration")
    async def my_team(self, interaction: discord.Interaction):
        """View current team profile and today's registration."""
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        profile = team_profile.get_profile(owner_id)
        reg = reg_model.get_registration(owner_id, event_id)

        if not profile and not reg:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ No Team Found",
                    f"You haven't registered a team yet.\n"
                    f"Use the Register button or `/register` to get started!"
                ),
                ephemeral=True
            )
            return

        # Build profile info
        desc_parts = []
        if profile:
            players = profile.get("players", [])
            roster_text = "\n".join([f"  │  ✦ {p}" for p in players])
            desc_parts.append(
                f"╭── 👥 **Saved Profile** ──╮\n"
                f"│  🏷️ **Team:** `{profile.get('team_name', '?')}`\n"
                f"│\n{roster_text}\n╰───────────────────────╯"
            )

        if reg:
            group_id = reg.get("group_id", "???")
            status = reg.get("status", "registered")
            status_icon = "🟢" if status == "registered" else "🔴"
            desc_parts.append(
                f"╭── 📅 **Today's Registration** ──╮\n"
                f"│  📍 **Group:** `{group_id}`\n"
                f"│  📊 **Status:** {status_icon} `{status.upper()}`\n"
                f"╰────────────────────────────────╯"
            )
        else:
            desc_parts.append("\n*Not registered for today yet.*")

        embed = make_embed(
            f"🏷️ My Team",
            "\n\n".join(desc_parts),
            Theme.TEAL
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(RegistrationCog(bot))
