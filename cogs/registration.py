"""
Mack Bot  - Registration Cog
Handles the full registration flow:

- /setup command (permanent embed + register button)
- 30-Day "Already Registered" intercept
- 3-step modal chain: Modal 1 (Team Info) → Modal 2 (Player UIDs) → Teammate Select
- Atomic slot claim + public receipt
"""

import datetime
import asyncio
import discord
from discord.ext import commands
from discord import app_commands, ui

from config import Theme, TIMEZONE_OFFSET, REGISTRATION_OPEN_HOUR, REGISTRATION_OPEN_MINUTE
from utils.embeds import (
    make_embed, error_embed, success_embed, build_roster_embed,
    build_slot_availability_embed, build_registration_receipt_embed,
    build_registration_board_embed
)
from models import team_profile, group as group_model, registration as reg_model, punishment
from database import get_config, set_config
from utils.updater import update_group_roster, update_registration_board

# In-memory cache for multi-step registrations to bridge Step 1 and Step 2
registration_cache = {}

async def is_registration_open():
    """Check if registration is open based on database configuration or defaults."""
    try:
        open_hour = await asyncio.to_thread(get_config, "registration_open_hour", REGISTRATION_OPEN_HOUR)
        open_minute = await asyncio.to_thread(get_config, "registration_open_minute", REGISTRATION_OPEN_MINUTE)
    except Exception as e:
        print(f"⚠️ Error fetching registration config: {e}", flush=True)
        open_hour = REGISTRATION_OPEN_HOUR
        open_minute = REGISTRATION_OPEN_MINUTE

    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    open_time = local_now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
    
    if local_now < open_time:
        return False, open_hour, open_minute, local_now
    return True, open_hour, open_minute, local_now

# ═══════════════════ HELPERS ═══════════════════

def get_today_event_id():
    """Get today\'s event ID based on IST date."""
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")

# ═══════════════════ MODAL 1: TEAM INFO (Step 1/3) ═══════════════════

class TeamInfoModal(ui.Modal, title="📋 Team Registration — Step 1/3"):
    """Modal 1: Basic team details."""

    team_name = ui.TextInput(
        label="Team Name",
        placeholder="Enter a unique team name (e.g. Galaxy Crows)",
        max_length=50,
        style=discord.TextStyle.short
    )
    owner_name = ui.TextInput(
        label="Owner / IGL Name",
        placeholder="Your real name or IGN",
        max_length=50,
        style=discord.TextStyle.short
    )
    email = ui.TextInput(
        label="Email Address",
        placeholder="your@email.com",
        max_length=100,
        style=discord.TextStyle.short,
        required=False
    )
    contact = ui.TextInput(
        label="Contact Number",
        placeholder="+91 XXXXX XXXXX",
        max_length=20,
        style=discord.TextStyle.short,
        required=False
    )

    def __init__(self, prefill: dict = None):
        super().__init__()
        if prefill:
            self.team_name.default = prefill.get("team_name", "")
            self.owner_name.default = prefill.get("owner_name", "")
            self.email.default = prefill.get("email", "")
            self.contact.default = prefill.get("contact", "")

    async def on_submit(self, interaction: discord.Interaction):
        owner_id = str(interaction.user.id)
        name = self.team_name.value.strip()

        if not name:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Team Name", "Team name cannot be empty."),
                ephemeral=True
            )
            return

        # Check team name uniqueness asynchronously
        is_dup, existing_owner = await asyncio.to_thread(
            team_profile.check_duplicate_team_name, name, exclude_owner_id=owner_id
        )
        if is_dup:
            await interaction.response.send_message(
                embed=error_embed("❌ Name Taken", f"Team name **{name}** is already registered by another squad!"),
                ephemeral=True
            )
            return

        is_edit = getattr(self, '_is_edit', False)

        # Store data temporarily in the cache
        registration_cache[interaction.user.id] = {
            "team_name": name,
            "owner_name": self.owner_name.value.strip(),
            "email": self.email.value.strip(),
            "contact": self.contact.value.strip(),
            "is_edit": is_edit,
        }

        # Show Step 1 summary embed with button bridge
        embed = make_embed(
            "📋 Team Registration — Step 1/3 Complete",
            f"✅ **Step 1 details captured!**\n\n"
            f"╭── 📋 **Team Details** ──╮\n"
            f"│  🏷️ **Team Name:** `{name}`\n"
            f"│  👤 **Owner/IGL:** `{self.owner_name.value.strip()}`\n"
            f"│  📧 **Email:** `{self.email.value.strip() if self.email.value.strip() else 'Not provided'}`\n"
            f"│  📞 **Contact:** `{self.contact.value.strip() if self.contact.value.strip() else 'Not provided'}`\n"
            f"╰───────────────────────╯\n\n"
            f"{Theme.THIN_SEP}\n"
            f"Please click the **Proceed to Step 2** button below to enter your player roster details.",
            Theme.ACCENT,
            "Step 1 of 3 — Team Info"
        )
        await interaction.response.send_message(
            embed=embed,
            view=ProceedToStep2View(interaction.user.id),
            ephemeral=True
        )

class ProceedToStep2View(ui.View):
    """View with a button to proceed to Step 2/3 modal."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id

    @ui.button(
        label="Proceed to Step 2",
        style=discord.ButtonStyle.primary,
        emoji="➡️",
        custom_id="proceed_to_step_2_btn"
    )
    async def proceed_to_step_2(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Access Denied", "This registration session belongs to someone else."),
                ephemeral=True
            )
            return

        cached_data = registration_cache.get(interaction.user.id)
        if not cached_data:
            await interaction.response.send_message(
                embed=error_embed("❌ Session Expired", "Your registration session has expired. Please start over."),
                ephemeral=True
            )
            return

        user_id = str(interaction.user.id)
        is_edit = cached_data.get("is_edit", False)
        prefill_players = {}
        if is_edit:
            profile = await asyncio.to_thread(team_profile.get_profile, user_id)
            if profile:
                uids = profile.get("player_uids", [])
                igns = profile.get("player_igns", [])
                for i in range(min(4, len(uids))):
                    prefill_players[f"uid_{i+1}"] = uids[i] if i < len(uids) else ""
                    prefill_players[f"ign_{i+1}"] = igns[i] if i < len(igns) else ""
            modal2 = PlayerDetailsModal(cached_data, prefill_players) if profile else PlayerDetailsModal(cached_data)
        else:
            modal2 = PlayerDetailsModal(cached_data)

        await interaction.response.send_modal(modal2)

# ═══════════════════ MODAL 2: PLAYER DETAILS (Step 2/3) ═══════════════════

class PlayerDetailsModal(ui.Modal, title="🎮 Player Roster — Step 2/3"):
    """Modal 2: Player UIDs and IGNs."""

    player1 = ui.TextInput(
        label="Player 1 — UID | IGN",
        placeholder="e.g. 5123456789 | ProPlayer1",
        max_length=80,
        style=discord.TextStyle.short
    )
    player2 = ui.TextInput(
        label="Player 2 — UID | IGN",
        placeholder="e.g. 5987654321 | ProPlayer2",
        max_length=80,
        style=discord.TextStyle.short
    )
    player3 = ui.TextInput(
        label="Player 3 — UID | IGN",
        placeholder="e.g. 5111222333 | ProPlayer3",
        max_length=80,
        style=discord.TextStyle.short
    )
    player4 = ui.TextInput(
        label="Player 4 — UID | IGN",
        placeholder="e.g. 5444555666 | ProPlayer4",
        max_length=80,
        style=discord.TextStyle.short
    )

    def __init__(self, team_data: dict, prefill: dict = None):
        super().__init__()
        self.team_data = team_data
        if prefill:
            uids = [prefill.get(f"uid_{i}", "") for i in range(1, 5)]
            igns = [prefill.get(f"ign_{i}", "") for i in range(1, 5)]
            defaults = []
            for i in range(4):
                u = uids[i] if i < len(uids) else ""
                g = igns[i] if i < len(igns) else ""
                if u or g:
                    defaults.append(f"{u} | {g}")
                else:
                    defaults.append("")
            if len(defaults) > 0 and defaults[0]:
                self.player1.default = defaults[0]
            if len(defaults) > 1 and defaults[1]:
                self.player2.default = defaults[1]
            if len(defaults) > 2 and defaults[2]:
                self.player3.default = defaults[2]
            if len(defaults) > 3 and defaults[3]:
                self.player4.default = defaults[3]

    def _parse_player(self, value: str):
        """Parse 'UID | IGN' format. Returns (uid, ign) tuple."""
        if "|" in value:
            parts = value.split("|", 1)
            return parts[0].strip(), parts[1].strip()
        return value.strip(), value.strip()

    async def on_submit(self, interaction: discord.Interaction):
        owner_id = str(interaction.user.id)

        # Parse all 4 players
        players_raw = [
            self.player1.value.strip(),
            self.player2.value.strip(),
            self.player3.value.strip(),
            self.player4.value.strip(),
        ]

        player_uids = []
        player_igns = []
        player_list = []

        for raw in players_raw:
            if not raw:
                continue
            uid, ign = self._parse_player(raw)
            player_uids.append(uid)
            player_igns.append(ign)
            player_list.append(ign)

        if len(player_list) < 1:
            await interaction.response.send_message(
                embed=error_embed("❌ No Players", "You must enter at least one player."),
                ephemeral=True
            )
            return

        # Save the team profile with all data (including new fields) asynchronously
        await asyncio.to_thread(
            team_profile.save_profile,
            owner_id=owner_id,
            team_name=self.team_data["team_name"],
            players=player_list,
            owner_name=self.team_data.get("owner_name"),
            email=self.team_data.get("email"),
            contact=self.team_data.get("contact"),
            player_uids=player_uids,
            player_igns=player_igns,
        )

        # Show teammate selection (Step 3)
        roster_text = "\n".join([f"  │  ✦ `{player_uids[i]}` — {player_igns[i]}" for i in range(len(player_igns))])
        embed = make_embed(
            "👥 Final Step: Select Teammates — Step 3/3",
            f"✅ Team **{self.team_data['team_name']}** profile saved!\n\n"
            f"╭── 📋 **Roster** ──╮\n{roster_text}\n╰───────────────────╯\n\n"
            f"{Theme.THIN_SEP}\n"
            f"**Next:** Select your **4 to 5 squad members** from the dropdown below.\n"
            f"You must include yourself.",
            Theme.ACCENT,
            "Step 3 of 3 — Select teammates"
        )
        await interaction.response.send_message(
            embed=embed,
            view=TeammateSelectView(self.team_data["team_name"], player_list, player_uids, player_igns),
            ephemeral=True
        )

# ═══════════════════ TEAMMATE SELECTION ═══════════════════

class ConfirmRegistrationView(ui.View):
    """View with a button to finalize registration."""

    def __init__(self, team_name, players, selected_members, player_uids=None, player_igns=None):
        super().__init__(timeout=120)
        self.team_name = team_name
        self.players = players
        self.selected_members = selected_members
        self.player_uids = player_uids or []
        self.player_igns = player_igns or []

    @ui.button(
        label="Confirm & Complete Registration",
        style=discord.ButtonStyle.primary,
        emoji="✅",
        custom_id="finalize_reg_btn"
    )
    async def confirm_registration(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        # Check if banned asynchronously
        is_ban, ban_doc = await asyncio.to_thread(punishment.is_banned, owner_id)
        if is_ban:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "You are banned and cannot register."),
                ephemeral=True
            )
            return

        # Check if already registered today asynchronously
        is_registered = await asyncio.to_thread(reg_model.is_already_registered, owner_id, event_id)
        if is_registered:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "You are already registered for today."),
                ephemeral=True
            )
            return

        # Check teammate status asynchronously
        for m in self.selected_members:
            is_reg, existing_team = await asyncio.to_thread(reg_model.is_teammate_registered, str(m.id), event_id)
            if is_reg:
                await interaction.response.send_message(
                    embed=error_embed("❌ Error", f"{m.mention} is already registered in team **{existing_team}**."),
                    ephemeral=True
                )
                return

        # Check group availability asynchronously
        available_groups = await asyncio.to_thread(group_model.get_open_groups, event_id)
        if not available_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ All Groups Full", "All groups for today are completely full."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Atomic slot claim asynchronously
        assigned_group = await asyncio.to_thread(group_model.claim_slot, event_id)
        if not assigned_group:
            await interaction.followup.send(
                embed=error_embed("❌ Claim Failed", "All groups filled up while processing your request."),
                ephemeral=True
            )
            return

        # Create registration asynchronously
        teammate_ids = [str(m.id) for m in self.selected_members]
        await asyncio.to_thread(
            reg_model.create_registration,
            owner_id=owner_id,
            event_id=event_id,
            group_id=assigned_group["group_id"],
            team_name=self.team_name,
            players=self.players,
            teammate_ids=teammate_ids,
            slot_number=assigned_group["current_count"]
        )

        # Save teammate IDs to profile asynchronously
        await asyncio.to_thread(
            team_profile.save_profile,
            owner_id, self.team_name, self.players, teammate_ids,
            player_uids=self.player_uids, player_igns=self.player_igns
        )

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

        # Build premium success embed
        success = make_embed(
            "🎯 Registration Complete!",
            f"🏆 **Team** — `{self.team_name}`\n"
            f"⚙️ **Assigned Group** — `{group_id}`\n"
            f"🎰 **Roster Slot** — `Slot {assigned_group['current_count']:02d}`\n"
            f"📅 **Date** — `{event_date_display}`\n\n"

            f"╭── 🎮 **Match Schedule** ──╮\n"
            f"│  **Match 1:** `{m1.get('start', 'TBD')}` │ IDP `{m1.get('idp', 'TBD')}` │ Map `{m1.get('map', 'TBD')}`\n"
            f"│  **Match 2:** `{m2.get('start', 'TBD')}` │ IDP `{m2.get('idp', 'TBD')}` │ Map `{m2.get('map', 'TBD')}`\n"
            f"╰────────────────────────────╯\n\n"
            f"Please join your group channel when it's available.\n"
            f"🎱 **Best of luck 👊 for your matches!!**",
            Theme.SUCCESS,
            "Mack Bot Tortuga 🚀 2027 Edition"
        )
        await interaction.followup.send(embed=success, ephemeral=True)

        # Refresh the roster in the group channel
        await update_group_roster(interaction.guild, event_id, group_id)

        # Refresh the slot availability embed in register channel
        await update_registration_board(interaction.guild, event_id)

        # Post public receipt to #registered-teams log channel
        await self._post_public_receipt(
            interaction.guild, self.team_name, group_id,
            self.players, self.player_uids, self.player_igns,
            self.selected_members, event_date_display
        )

    async def _post_public_receipt(self, guild, team_name, group_id, players,
                                    player_uids, player_igns, members, date_display):
        """Post a confirmation receipt to #registered-teams."""
        from database import get_channel_config

        log_channel_id = await asyncio.to_thread(get_channel_config, "registered_teams")
        if not log_channel_id:
            return

        channel = guild.get_channel(log_channel_id)
        if not channel:
            return

        embed = build_registration_receipt_embed(
            team_name, group_id, players, player_uids,
            player_igns, members, date_display
        )
        await channel.send(embed=embed)


class TeammateSelect(ui.UserSelect):
    """Dropdown to select 4 to 5 squad members."""

    def __init__(self, team_name, players, player_uids=None, player_igns=None):
        self.team_name = team_name
        self.players = players
        self.player_uids = player_uids or []
        self.player_igns = player_igns or []
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

        # Validation: check if any teammate is already registered today asynchronously
        for m in members:
            is_reg, existing_team = await asyncio.to_thread(reg_model.is_teammate_registered, str(m.id), event_id)
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

        # Check if groups are available asynchronously
        available_groups = await asyncio.to_thread(group_model.get_open_groups, event_id)
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
        confirm_view = ConfirmRegistrationView(
            self.team_name, self.players, members,
            self.player_uids, self.player_igns
        )
        await interaction.response.send_message(
            content="Teammates selected. Click the button below to Finalize Registration.",
            view=confirm_view,
            ephemeral=True
        )


class TeammateSelectView(ui.View):
    """View containing the teammate select dropdown."""

    def __init__(self, team_name, players, player_uids=None, player_igns=None):
        super().__init__(timeout=120)
        self.add_item(TeammateSelect(team_name, players, player_uids, player_igns))


# ═══════════════════ SAVED PROFILE VIEWS ═══════════════════

class SavedProfileView(ui.View):
    """Shown when a returning player has a saved team profile within 30 days."""

    def __init__(self, profile):
        super().__init__(timeout=120)
        self.profile = profile

    @ui.button(label="Use Old Team", style=discord.ButtonStyle.secondary, emoji="📂", row=0)
    async def use_saved(self, interaction: discord.Interaction, button: ui.Button):
        """Use saved profile → skip Modal 1 & 2, go straight to teammate selection."""
        team_name = self.profile["team_name"]
        players = self.profile.get("players", [])
        player_uids = self.profile.get("player_uids", [])
        player_igns = self.profile.get("player_igns", [])

        # Show teammate selection with saved data
        uid_display = ""
        if player_uids and player_igns:
            lines = [f"  │  ✦ `{player_uids[i]}` — {player_igns[i]}" for i in range(min(len(player_uids), len(player_igns)))]
            uid_display = "\n".join(lines)
        else:
            uid_display = "\n".join([f"  │  ✦ {p}" for p in players])

        embed = make_embed(
            "👥 Final Step: Select Teammates",
            f"Your team **{team_name}** has been loaded successfully!\n\n"
            f"╭── 📋 **Roster** ──╮\n{uid_display}\n╰───────────────────╯\n\n"
            f"{Theme.THIN_SEP}\n"
            f"**Next:** Select your **4 to 5 squad members** from the dropdown below.\n"
            f"You must include yourself.",
            Theme.ACCENT,
            "Step 3 of 3 — Select teammates"
        )
        await interaction.response.send_message(
            embed=embed,
            view=TeammateSelectView(team_name, players, player_uids, player_igns),
            ephemeral=True
        )

    @ui.button(label="Edit Team", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def edit_team(self, interaction: discord.Interaction, button: ui.Button):
        """Edit → opens Modal 1 pre-filled with saved data."""
        prefill = {
            "team_name": self.profile.get("team_name", ""),
            "owner_name": self.profile.get("owner_name", ""),
            "email": self.profile.get("email", ""),
            "contact": self.profile.get("contact", ""),
        }
        modal = TeamInfoModal(prefill=prefill)
        modal._is_edit = True
        await interaction.response.send_modal(modal)

    @ui.button(label="New Team", style=discord.ButtonStyle.secondary, emoji="✨", row=1)
    async def new_team(self, interaction: discord.Interaction, button: ui.Button):
        """New → wipes old data, opens blank Modal 1."""
        # Delete old profile asynchronously
        owner_id = str(interaction.user.id)
        await asyncio.to_thread(team_profile.delete_profile, owner_id)
        await interaction.response.send_modal(TeamInfoModal())


# ═══════════════════ PERSISTENT REGISTER BUTTON ═══════════════════

class PersistentRegisterView(ui.View):
    """The always-alive Register button posted in #register-here."""

    def __init__(self, locked=False):
        super().__init__(timeout=None)
        self.clear_items()

        if locked:
            btn = ui.Button(
                label="🔒 Registration Closed",
                style=discord.ButtonStyle.secondary,
                custom_id="tortuga_register_btn",
                disabled=True,
                row=0
            )
            self.add_item(btn)

            notify_btn = ui.Button(
                label="🔔 Notify Me",
                style=discord.ButtonStyle.primary,
                custom_id="tortuga_notify_open_btn",
                row=0
            )
            notify_btn.callback = self._notify_callback
            self.add_item(notify_btn)
        else:
            btn = ui.Button(
                label="📥 Register Team",
                style=discord.ButtonStyle.green,
                custom_id="tortuga_register_btn",
                row=0
            )
            btn.callback = self._register_callback
            self.add_item(btn)

            reminder_btn = ui.Button(
                label="🔔 Match Reminder",
                style=discord.ButtonStyle.secondary,
                custom_id="tortuga_match_reminder_btn",
                row=0
            )
            reminder_btn.callback = self._reminder_callback
            self.add_item(reminder_btn)

    async def _notify_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        await interaction.response.defer(ephemeral=True)

        try:
            subscribers = await asyncio.to_thread(get_config, "registration_open_subscribers", [])
            if subscribers is None:
                subscribers = []
            if user_id in subscribers:
                subscribers.remove(user_id)
                await asyncio.to_thread(set_config, "registration_open_subscribers", subscribers)
                await interaction.followup.send(
                    embed=success_embed(
                        "🔕 Notification Cancelled",
                        "You will no longer receive a DM when registration opens."
                    ),
                    ephemeral=True
                )
            else:
                subscribers.append(user_id)
                await asyncio.to_thread(set_config, "registration_open_subscribers", subscribers)
                await interaction.followup.send(
                    embed=success_embed(
                        "🔔 Notification Set",
                        "You will receive a DM when registration opens!"
                    ),
                    ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(
                embed=error_embed("❌ Error", f"Failed to update notify settings: {e}"),
                ephemeral=True
            )

    async def _reminder_callback(self, interaction: discord.Interaction):
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()
        await interaction.response.defer(ephemeral=True)

        # Check if the user is registered today asynchronously
        reg = await asyncio.to_thread(reg_model.get_registration, owner_id, event_id)
        if not reg:
            await interaction.followup.send(
                embed=error_embed(
                    "❌ Not Registered",
                    "You must register your team first before setting a match reminder!"
                ),
                ephemeral=True
            )
            return

        current_state = reg.get("dm_reminder", False)
        new_state = not current_state

        from database import registrations as registrations_collection
        await asyncio.to_thread(
            registrations_collection.update_one,
            {"owner_id": owner_id, "event_id": event_id, "status": "registered"},
            {"$set": {"dm_reminder": new_state}}
        )

        status_text = "ENABLED" if new_state else "DISABLED"
        embed = make_embed(
            "🔔 Match Reminder Updated",
            f"Direct Message reminders are now **{status_text}** for your team **{reg['team_name']}**.\n\n"
            f"You will receive a direct message 30 minutes before your match begins.",
            Theme.SUCCESS if new_state else Theme.WARNING
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _register_callback(self, interaction: discord.Interaction):
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        # Check registration open time dynamically and asynchronously
        is_open, open_h, open_m, current_t = await is_registration_open()
        if not is_open:
            await interaction.response.send_message(
                embed=error_embed(
                    "🔒 Registration Closed",
                    f"Registration opens daily at **{open_h:02d}:{open_m:02d} AM IST**.\n"
                    f"Current time: **{current_t.strftime('%I:%M %p IST')}**"
                ),
                ephemeral=True
            )
            return

        # Check if banned asynchronously
        is_ban, ban_doc = await asyncio.to_thread(punishment.is_banned, owner_id)
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

        # Check if already registered today asynchronously
        existing = await asyncio.to_thread(reg_model.get_registration, owner_id, event_id)
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

        # Check if groups are provisioned asynchronously
        available = await asyncio.to_thread(group_model.get_open_groups, event_id)
        all_groups = await asyncio.to_thread(group_model.get_all_groups, event_id)
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

        # "Already Registered" Intercept — check for saved profile (30-day memory) asynchronously
        profile = await asyncio.to_thread(team_profile.get_profile, owner_id)
        if profile:
            # Condition B: Valid profile exists within 30 days
            team_name = profile.get("team_name", "Unknown")
            owner_name = profile.get("owner_name", "")
            player_count = len(profile.get("players", []))

            embed = make_embed(
                "👋 Welcome Back!",
                f"I found a saved profile for you.\n\n"
                f"╭── 📋 **Saved Profile** ──╮\n"
                f"│  🏷️ **Team:** `{team_name}`\n"
                f"│  👤 **Owner:** `{owner_name}`\n"
                f"│  👥 **Players:** `{player_count}`\n"
                f"╰───────────────────────╯\n\n"
                f"Choose an option below:",
                Theme.ACCENT,
                "Profile found — 30-day memory"
            )
            await interaction.response.send_message(
                embed=embed,
                view=SavedProfileView(profile),
                ephemeral=True
            )
        else:
            # Condition A: New user or expired — open Modal 1 immediately
            await interaction.response.send_modal(TeamInfoModal())


# ═══════════════════ COG ═══════════════════

class RegistrationCog(commands.Cog):
    """Handles team registration flow."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Register persistent views on cog load."""
        self.bot.add_view(PersistentRegisterView(locked=False))
        self.bot.add_view(PersistentRegisterView(locked=True))

    # ─────────────── /setup COMMAND ───────────────

    @app_commands.command(
        name="setup",
        description="[Admin] Drop the permanent registration board and button in #register-here"
    )
    @app_commands.describe(
        channel="The #register-here channel to set up"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_cmd(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Setup command: drops the permanent Slot Embed and Register buttons."""
        from database import set_config, set_channel_config

        await interaction.response.defer(ephemeral=True)

        # Set channel permissions: @everyone -> Send Messages ❌
        try:
            await channel.set_permissions(
                interaction.guild.default_role,
                send_messages=False,
                read_messages=True,
                read_message_history=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("❌ Permission Error", "I don't have permission to modify channel settings."),
                ephemeral=True
            )
            return

        # Ensure bot can send messages
        try:
            await channel.set_permissions(
                interaction.guild.me,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_messages=True
            )
        except discord.Forbidden:
            pass

        # Build and send the permanent registration board
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)
        embed = build_registration_board_embed(all_groups)

        view = PersistentRegisterView(locked=False)
        msg = await channel.send(embed=embed, view=view)

        # Save the message ID and channel config
        set_config("slot_message_id", msg.id)
        set_channel_config("register", channel.id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Registration Board Deployed!",
                f"Permanent registration embed + button posted in {channel.mention}.\n\n"
                f"**Message ID saved:** `{msg.id}`\n"
                f"**Channel locked:** @everyone Send Messages ❌"
            ),
            ephemeral=True
        )

    # ─────────────── /register SLASH COMMAND ───────────────

    @app_commands.command(name="register", description="Register your team for today's scrims")
    async def register_slash(self, interaction: discord.Interaction):
        """Slash command alternative to the button — opens Modal 1."""
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        # Check registration open time dynamically and asynchronously
        is_open, open_h, open_m, current_t = await is_registration_open()
        if not is_open:
            await interaction.response.send_message(
                embed=error_embed(
                    "🔒 Registration Closed",
                    f"Registration opens daily at **{open_h:02d}:{open_m:02d} AM IST**.\n"
                    f"Current time: **{current_t.strftime('%I:%M %p IST')}**"
                ),
                ephemeral=True
            )
            return

        # Check ban asynchronously
        is_ban, _ = await asyncio.to_thread(punishment.is_banned, owner_id)
        if is_ban:
            await interaction.response.send_message(
                embed=error_embed("⛔ Banned", "You are banned from scrims."),
                ephemeral=True
            )
            return

        # Check already registered asynchronously
        existing = await asyncio.to_thread(reg_model.get_registration, owner_id, event_id)
        if existing:
            await interaction.response.send_message(
                embed=error_embed("⚠️ Already Registered", "You're already registered for today."),
                ephemeral=True
            )
            return

        # Check saved profile asynchronously
        profile = await asyncio.to_thread(team_profile.get_profile, owner_id)
        if profile:
            embed = make_embed(
                "👋 Welcome Back!",
                f"I found a saved profile for **{profile.get('team_name', '?')}**.\nChoose an option:",
                Theme.ACCENT
            )
            await interaction.response.send_message(
                embed=embed,
                view=SavedProfileView(profile),
                ephemeral=True
            )
        else:
            await interaction.response.send_modal(TeamInfoModal())

    # ─────────────── /myteam COMMAND ───────────────

    @app_commands.command(name="myteam", description="View your team info and today's registration")
    async def my_team(self, interaction: discord.Interaction):
        """View current team profile and today's registration."""
        owner_id = str(interaction.user.id)
        event_id = get_today_event_id()

        profile = await asyncio.to_thread(team_profile.get_profile, owner_id)
        reg = await asyncio.to_thread(reg_model.get_registration, owner_id, event_id)

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
            player_uids = profile.get("player_uids", [])
            player_igns = profile.get("player_igns", [])
            owner_name = profile.get("owner_name", "")

            if player_uids and player_igns:
                roster_text = "\n".join([
                    f"  │  ✦ `{player_uids[i]}` — {player_igns[i]}"
                    for i in range(min(len(player_uids), len(player_igns)))
                ])
            else:
                roster_text = "\n".join([f"  │  ✦ {p}" for p in players])

            expires = profile.get("expires_at", "")
            exp_display = ""
            if expires:
                try:
                    exp_dt = datetime.datetime.fromisoformat(expires)
                    exp_display = f"\n│  ⏳ **Expires:** `{exp_dt.strftime('%Y-%m-%d')}`"
                except Exception:
                    pass

            desc_parts.append(
                f"╭── 👥 **Saved Profile** ──╮\n"
                f"│  🏷️ **Team:** `{profile.get('team_name', '?')}`\n"
                f"│  👤 **Owner:** `{owner_name}`{exp_display}\n"
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
