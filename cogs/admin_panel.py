"""
Mack Bot — Admin Panel Cog
Contains:
  - GroupControlPanelView: 3-row persistent panel in every group channel
  - /panel command for admin overview
  - /config, /viewconfig, /unban, /banlist commands
  - Modals for Edit Match, Move Team, Punish Team
"""

import datetime
import asyncio
import discord
from discord.ext import commands
from discord import app_commands, ui

from config import Theme, TIMEZONE_OFFSET, DEFAULT_LOCK_MINUTES, get_rank_emoji, DEFAULT_RESERVED_SLOTS
from utils.embeds import make_embed, error_embed, success_embed, build_roster_embed
from utils.permissions import grant_group_access, revoke_group_access
from models import group as group_model, registration as reg_model, punishment
from database import get_config, set_config, get_channel_config, set_channel_config
from utils.updater import update_group_roster, update_registration_board


# ═══════════════════ HELPERS ═══════════════════

def get_today_event_id():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")


def is_admin(member: discord.Member) -> bool:
    """Check if a member has admin permissions."""
    return member.guild_permissions.administrator


# ═══════════════════ MODALS ═══════════════════

class EditMatchModal(ui.Modal, title="✏️ Edit Match Details"):
    """Modal for editing a group's match IDP, start time, and map."""

    match_num = ui.TextInput(label="Match Number (1 or 2)", placeholder="1", max_length=1)
    idp_time = ui.TextInput(label="IDP Time", placeholder="2:00 PM", required=False)
    start_time = ui.TextInput(label="Start Time", placeholder="2:10 PM", required=False)
    map_name = ui.TextInput(label="Map", placeholder="Erangel", required=False)

    def __init__(self, event_id: str, group_id: str):
        super().__init__()
        self.event_id = event_id
        self.group_id = group_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            match_n = int(self.match_num.value.strip())
            if match_n not in (1, 2):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Match number must be 1 or 2."),
                ephemeral=True
            )
            return

        group_doc = group_model.get_group(self.event_id, self.group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{self.group_id}` not found for today."),
                ephemeral=True
            )
            return

        details = {}
        if self.idp_time.value.strip():
            details["idp"] = self.idp_time.value.strip()
        if self.start_time.value.strip():
            details["start"] = self.start_time.value.strip()
        if self.map_name.value.strip():
            details["map"] = self.map_name.value.strip()

        if not details:
            await interaction.response.send_message(
                embed=error_embed("❌ Nothing Changed", "Fill in at least one field to update."),
                ephemeral=True
            )
            return

        group_model.update_match_details(self.event_id, self.group_id, match_n, details)

        updates = "\n".join([f"  ◆ **{k}:** `{v}`" for k, v in details.items()])
        await interaction.response.send_message(
            embed=success_embed(
                f"✅ Match {match_n} Updated — Group {self.group_id}",
                f"{Theme.SEP}\n\n{updates}\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )


class MoveTeamModal(ui.Modal, title="🔀 Move Team"):
    """Modal for moving a team between groups (admin override)."""

    user_id_input = ui.TextInput(label="Team Owner's User ID", placeholder="123456789012345678")
    target_group = ui.TextInput(label="Target Group ID", placeholder="G0005")

    async def on_submit(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        owner_id = self.user_id_input.value.strip()
        new_gid = self.target_group.value.strip().upper()

        reg = reg_model.get_registration(owner_id, event_id)
        if not reg:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"No registration found for user `{owner_id}` today."),
                ephemeral=True
            )
            return

        old_gid = reg.get("group_id")
        new_group = group_model.get_group(event_id, new_gid)
        if not new_group:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{new_gid}` not found."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Atomic move (admin override — ignores lock)
        from database import groups as groups_collection
        from pymongo import ReturnDocument
        new_group_doc = groups_collection.find_one_and_update(
            {"event_id": event_id, "group_id": new_gid},
            {"$inc": {"current_count": 1}},
            return_document=ReturnDocument.AFTER
        )
        group_model.release_slot(event_id, old_gid)
        reg_model.move_registration(owner_id, event_id, new_gid, new_group_doc.get("current_count"))

        # Swap roles
        guild = interaction.guild
        old_group_doc = group_model.get_group(event_id, old_gid)
        new_group_doc = group_model.get_group(event_id, new_gid)

        old_role = guild.get_role(old_group_doc.get("role_id")) if old_group_doc else None
        new_role = guild.get_role(new_group_doc.get("role_id")) if new_group_doc else None

        teammate_ids = reg.get("teammate_ids", [owner_id])
        for tid in teammate_ids:
            member = guild.get_member(int(tid))
            if member:
                if old_role:
                    try: await member.remove_roles(old_role)
                    except Exception: pass
                if new_role:
                    try: await member.add_roles(new_role)
                    except Exception: pass

        team_name = reg.get("team_name", "???")

        # Refresh rosters and board
        await update_group_roster(guild, event_id, old_gid)
        await update_group_roster(guild, event_id, new_gid)
        await update_registration_board(guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Team Moved",
                f"**{team_name}** moved from `{old_gid}` → `{new_gid}`"
            ),
            ephemeral=True
        )


class PunishModal(ui.Modal, title="🔨 Punish Team"):
    """Modal for banning a team owner."""

    user_id_input = ui.TextInput(label="User ID to Ban", placeholder="123456789012345678")
    days_input = ui.TextInput(label="Ban Duration (days, 0 = permanent)", placeholder="2", max_length=3)
    reason_input = ui.TextInput(
        label="Reason",
        placeholder="e.g. Wasted slot — no-show without cancelling",
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        owner_id = self.user_id_input.value.strip()

        try:
            days = int(self.days_input.value.strip())
            if days < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Days must be 0 or positive."),
                ephemeral=True
            )
            return

        reason = self.reason_input.value.strip() or "No reason provided"
        admin_id = str(interaction.user.id)

        guild = interaction.guild
        member = guild.get_member(int(owner_id))
        username = str(member) if member else owner_id

        punishment.ban_user(owner_id, username, reason, days, admin_id)

        # Cancel today's registration if exists
        event_id = get_today_event_id()
        reg = reg_model.get_registration(owner_id, event_id)
        if reg:
            reg_model.cancel_registration(owner_id, event_id)
            group_model.release_slot(event_id, reg["group_id"])
            group_doc = group_model.get_group(event_id, reg["group_id"])
            if group_doc and member:
                role = guild.get_role(group_doc.get("role_id"))
                if role:
                    try: await member.remove_roles(role)
                    except: pass

        duration_str = "permanently" if days == 0 else f"for {days} days"
        embed = make_embed(
            "🔨 Player Banned",
            f"{Theme.SEP}\n\n"
            f"👤 **Player:** <@{owner_id}> (`{username}`)\n"
            f"⏱️ **Duration:** {duration_str}\n"
            f"📝 **Reason:** {reason}\n"
            f"**Banned by:** {interaction.user.mention}\n\n{Theme.SEP}",
            Theme.ERROR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Log
        log_ch_id = get_channel_config("admin_log")
        if log_ch_id:
            log_ch = guild.get_channel(log_ch_id)
            if log_ch:
                await log_ch.send(embed=embed)

        # DM the user
        if member:
            try:
                await member.send(embed=make_embed(
                    "🔨 You Have Been Banned",
                    f"{Theme.SEP}\n\n"
                    f"You have been banned from scrims {duration_str}.\n"
                    f"📝 **Reason:** {reason}\n\n"
                    f"*Contact an admin if you believe this is an error.*\n\n{Theme.SEP}",
                    Theme.ERROR
                ))
            except: pass


# ═══════════════════ GROUP CONTROL PANEL (Per-Channel) ═══════════════════

class GroupControlPanelView(ui.View):
    """
    Persistent 3-row control panel placed in every group channel.

    Row 1 (Admin Only):
      ⏰ Match Reminder | 📤 Publish Slot List
    Row 2 (Teams & Admins):
      🛠️ Manage Matches | 🔨 Punish Team (Admin Only)
    Row 3 (Admin Only):
      🌟 Qualified Teams
    """

    def __init__(self, event_id: str = None, group_id: str = None):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.group_id = group_id

    # ── Row 1: Admin Only ──

    @ui.button(label="⏰ Match Reminder", style=discord.ButtonStyle.primary, row=0,
               custom_id="gcp_reminder")
    async def reminder_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can send match reminders."),
                ephemeral=True
            )
            return

        event_id = self.event_id or get_today_event_id()
        group_id = self._resolve_group_id(interaction)

        cog = interaction.client.get_cog("RemindersCog")
        if cog and group_id:
            await cog.remind_group.callback(cog, interaction, group_id=group_id)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded or group not found."),
                ephemeral=True
            )

    @ui.button(label="📤 Publish Slot List", style=discord.ButtonStyle.secondary, row=0,
               custom_id="gcp_slotlist")
    async def slot_list_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can publish slot lists."),
                ephemeral=True
            )
            return

        event_id = self.event_id or get_today_event_id()
        group_id = self._resolve_group_id(interaction)

        cog = interaction.client.get_cog("RemindersCog")
        if cog and group_id:
            await cog.publish_slot_list.callback(cog, interaction, group_id=group_id)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded or group not found."),
                ephemeral=True
            )

    # ── Row 2: Teams & Admins ──

    @ui.button(label="🛠️ Manage Matches", style=discord.ButtonStyle.secondary, row=1,
               custom_id="gcp_manage")
    async def manage_btn(self, interaction: discord.Interaction, button: ui.Button):
        """
        For teams: shows Cancel Slot / Change Schedule
        For admins: shows Edit Match / Move Team
        """
        event_id = self.event_id or get_today_event_id()
        group_id = self._resolve_group_id(interaction)

        if is_admin(interaction.user):
            # Admin sub-menu
            view = AdminManageSubView(event_id, group_id)
            await interaction.response.send_message(
                embed=make_embed(
                    "🔧 Admin — Manage Matches",
                    f"{Theme.SEP}\n\n"
                    f"**✏️ Edit Match** — Change IDP, start time, or map\n"
                    f"**🔀 Move Team** — Admin override to move a team\n\n{Theme.SEP}",
                    Theme.ACCENT
                ),
                view=view, ephemeral=True
            )
        else:
            # Team sub-menu (Cancel / Change Schedule)
            owner_id = str(interaction.user.id)
            reg = reg_model.get_registration(owner_id, event_id)

            if not reg or reg.get("group_id") != group_id:
                await interaction.response.send_message(
                    embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                    ephemeral=True
                )
                return

            view = TeamManageSubView(event_id, group_id)
            await interaction.response.send_message(
                embed=make_embed(
                    "🛠️ Manage Your Match",
                    f"{Theme.SEP}\n\n"
                    f"**❌ Cancel Slot** — Remove your team from this group\n"
                    f"**🔄 Change Schedule** — Move to a different group\n\n{Theme.SEP}",
                    Theme.ACCENT
                ),
                view=view, ephemeral=True
            )

    @ui.button(label="🔨 Punish Team", style=discord.ButtonStyle.danger, row=1,
               custom_id="gcp_punish")
    async def punish_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can punish teams."),
                ephemeral=True
            )
            return
        await interaction.response.send_modal(PunishModal())

    # ── Row 3: Admin Only ──

    @ui.button(label="🌟 Qualified Teams", style=discord.ButtonStyle.success, row=2,
               custom_id="gcp_qualified")
    async def qualified_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("⛔ Access Denied", "Only admins can view qualified teams."),
                ephemeral=True
            )
            return

        event_id = self.event_id or get_today_event_id()
        from database import match_results as results_collection
        results = list(results_collection.find({"event_id": event_id}))

        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Standings", "No match results recorded yet today."),
                ephemeral=True
            )
            return

        team_totals = {}
        for r in results:
            tk = r.get("team_key") or r.get("team_name", "").strip().lower()
            if not tk:
                continue
            if tk not in team_totals:
                team_totals[tk] = {
                    "team_name": r.get("team_name", "?"),
                    "total_kills": 0,
                    "total_points": 0,
                    "matches_played": 0,
                }
            team_totals[tk]["total_kills"] += r.get("kills", 0)
            team_totals[tk]["total_points"] += r.get("total_points", 0)
            team_totals[tk]["matches_played"] += 1

        sorted_teams = sorted(team_totals.values(), key=lambda x: (x["total_points"], x["total_kills"]), reverse=True)

        lines = []
        for rank, t in enumerate(sorted_teams[:16], 1):
            medal = get_rank_emoji(rank)
            lines.append(f"{medal} **{t['team_name']}** ─ `{t['total_points']}` pts │ 💀 `{t['total_kills']}` kills")

        embed = make_embed(
            "🏆 Top Qualifying Teams",
            f"{Theme.SEP}\n\n"
            f"Here are the top **{len(lines)}** teams qualifying based on current standings:\n\n"
            + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.GOLD
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _resolve_group_id(self, interaction):
        """Try to resolve the group_id from stored value or channel context."""
        if self.group_id:
            return self.group_id

        # Try to find group by channel_id
        event_id = self.event_id or get_today_event_id()
        from database import groups as groups_collection
        doc = groups_collection.find_one({
            "event_id": event_id,
            "channel_id": interaction.channel.id,
            "archived": {"$ne": True}
        })
        return doc["group_id"] if doc else None


# ═══════════════════ ADMIN MANAGE SUB-VIEW ═══════════════════

class AdminManageSubView(ui.View):
    """Sub-view for admin: Edit Match and Move Team buttons."""

    def __init__(self, event_id, group_id):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.group_id = group_id

    @ui.button(label="✏️ Edit Match Details", style=discord.ButtonStyle.primary)
    async def edit_match(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(EditMatchModal(self.event_id, self.group_id))

    @ui.button(label="🔀 Move Team", style=discord.ButtonStyle.secondary)
    async def move_team(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(MoveTeamModal())


# ═══════════════════ TEAM MANAGE SUB-VIEW ═══════════════════

class TeamManageSubView(ui.View):
    """Sub-view for teams: Cancel Slot and Change Schedule."""

    def __init__(self, event_id, group_id):
        super().__init__(timeout=60)
        self.event_id = event_id
        self.group_id = group_id

    @ui.button(label="❌ Cancel Slot", style=discord.ButtonStyle.danger)
    async def cancel_slot(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = self.event_id or get_today_event_id()

        # Check if the group is locked
        group_doc = group_model.get_group(event_id, self.group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Group not found."),
                ephemeral=True
            )
            return

        if group_doc.get("locked", False):
            lock_min = int(get_config("lock_minutes", DEFAULT_LOCK_MINUTES))
            await interaction.response.send_message(
                embed=error_embed(
                    "⛔ Locked",
                    f"{Theme.SEP}\n\n"
                    f"Cancellation is **locked** — less than {lock_min} minutes before match.\n"
                    f"Contact an admin if you need to withdraw.\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
            return

        # Verify ownership
        reg = reg_model.get_registration(owner_id, event_id)
        if not reg or reg.get("group_id") != self.group_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Cancel the registration
        cancelled = reg_model.cancel_registration(owner_id, event_id)
        if not cancelled:
            await interaction.followup.send(
                embed=error_embed("❌ Error", "Could not cancel your registration."),
                ephemeral=True
            )
            return

        # Release the group slot
        group_model.release_slot(event_id, self.group_id)

        # Remove group role
        guild = interaction.guild
        role = guild.get_role(group_doc.get("role_id"))
        if role:
            teammate_ids = cancelled.get("teammate_ids", [])
            for tid in teammate_ids:
                member = guild.get_member(int(tid))
                if member and role:
                    await revoke_group_access(member, role)

        # Refresh roster and board
        await update_group_roster(interaction.guild, event_id, self.group_id)
        await update_registration_board(interaction.guild, event_id)

        team_name = cancelled.get("team_name", "your team")
        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Cancelled",
                f"{Theme.SEP}\n\n"
                f"Team **{team_name}** has been removed from Group **{self.group_id}**.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @ui.button(label="🔄 Change Schedule", style=discord.ButtonStyle.secondary)
    async def change_schedule(self, interaction: discord.Interaction, button: ui.Button):
        owner_id = str(interaction.user.id)
        event_id = self.event_id or get_today_event_id()

        # Check if locked
        group_doc = group_model.get_group(event_id, self.group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Group not found."),
                ephemeral=True
            )
            return

        if group_doc.get("locked", False):
            await interaction.response.send_message(
                embed=error_embed("⛔ Locked", "Schedule changes are locked before match start."),
                ephemeral=True
            )
            return

        reg = reg_model.get_registration(owner_id, event_id)
        if not reg or reg.get("group_id") != self.group_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", "You don't have a registration in this group."),
                ephemeral=True
            )
            return

        # Show open groups
        open_groups = group_model.get_open_groups(event_id)
        open_groups = [g for g in open_groups if g["group_id"] != self.group_id]

        if not open_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Available Groups", "All other groups are full or locked."),
                ephemeral=True
            )
            return

        options = []
        for g in open_groups[:25]:
            gid = g["group_id"]
            reserved = g.get("reserved_slots", 0)
            pub_count = max(0, g["current_count"] - reserved)
            pub_cap = g["capacity"] - reserved
            m1 = g.get("match1", {}).get("start", "TBD")
            options.append(
                discord.SelectOption(
                    label=f"Group {gid}",
                    description=f"{pub_count}/{pub_cap} filled │ M1: {m1}",
                    value=gid,
                    emoji="📍"
                )
            )

        view = ChangeGroupSelectView(event_id, self.group_id, options)
        await interaction.response.send_message(
            embed=make_embed(
                "🔄 Change Group",
                f"{Theme.SEP}\n\nSelect a new group from the dropdown below.\n\n{Theme.SEP}",
                Theme.ACCENT
            ),
            view=view,
            ephemeral=True
        )

    async def _refresh_board(self, guild, event_id):
        """Refresh the registration board after a cancel."""
        from utils.embeds import build_registration_board_embed
        reg_channel_id = get_channel_config("register")
        if not reg_channel_id:
            return

        channel = guild.get_channel(reg_channel_id)
        if not channel:
            return

        all_groups = group_model.get_all_groups(event_id)
        embed = build_registration_board_embed(all_groups)

        slot_msg_id = get_config("slot_message_id")
        if slot_msg_id:
            try:
                msg = await channel.fetch_message(slot_msg_id)
                await msg.edit(embed=embed)
            except discord.NotFound:
                pass


# ═══════════════════ CHANGE GROUP DROPDOWN ═══════════════════

class ChangeGroupSelectDropdown(ui.Select):
    """Dropdown for picking a new group (team self-service)."""

    def __init__(self, event_id, current_group_id, options):
        self.event_id = event_id
        self.current_group_id = current_group_id
        super().__init__(
            placeholder="📍 Select new group…",
            min_values=1, max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        new_group_id = self.values[0]
        owner_id = str(interaction.user.id)
        event_id = self.event_id

        await interaction.response.defer(ephemeral=True)

        # Atomic move
        old_group, new_group = group_model.move_slot(event_id, self.current_group_id, new_group_id)
        if not new_group:
            await interaction.followup.send(
                embed=error_embed("❌ Move Failed", "The target group is now full."),
                ephemeral=True
            )
            return

        # Update registration
        reg_model.move_registration(owner_id, event_id, new_group_id, new_group["current_count"])

        # Swap roles
        guild = interaction.guild
        old_role = guild.get_role(old_group.get("role_id")) if old_group else None
        new_role = guild.get_role(new_group.get("role_id"))

        reg = reg_model.get_registration(owner_id, event_id)
        teammate_ids = reg.get("teammate_ids", []) if reg else [owner_id]

        for tid in teammate_ids:
            member = guild.get_member(int(tid))
            if member:
                if old_role:
                    await revoke_group_access(member, old_role)
                if new_role:
                    await grant_group_access(member, new_role)

        # Refresh both rosters and board
        await update_group_roster(interaction.guild, event_id, self.current_group_id)
        await update_group_roster(interaction.guild, event_id, new_group_id)
        await update_registration_board(interaction.guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Group Changed",
                f"{Theme.SEP}\n\n"
                f"Moved from **{self.current_group_id}** → **{new_group_id}**\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )


class ChangeGroupSelectView(ui.View):
    def __init__(self, event_id, current_group_id, options):
        super().__init__(timeout=60)
        self.add_item(ChangeGroupSelectDropdown(event_id, current_group_id, options))


# ═══════════════════ ADMIN PANEL COG ═══════════════════

class AdminPanelCog(commands.Cog):
    """Admin panel with /panel and /config commands."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Register the persistent group control panel view."""
        self.bot.add_view(GroupControlPanelView())

    @app_commands.command(name="panel", description="[Admin] Open the admin control panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def panel(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)
        total_regs = reg_model.count_registrations(event_id)
        total_capacity = sum(g.get("capacity", 0) for g in all_groups)
        locked_count = sum(1 for g in all_groups if g.get("locked"))

        embed = make_embed(
            "⚙️ Admin Control Panel",
            f"╭── 📊 **Today's Overview** ──╮\n"
            f"│  📅 **Event:** `{event_id}`\n"
            f"│  👥 **Groups:** `{len(all_groups)}`\n"
            f"│  📋 **Registrations:** `{total_regs}/{total_capacity}`\n"
            f"│  🔒 **Locked:** `{locked_count}/{len(all_groups)}`\n"
            f"│  ▓ **Roster Fill:** {Theme.bar(total_regs, total_capacity, 14)}\n"
            f"╰────────────────────────────╯\n\n"
            f"Use the buttons below to manage today's scrims.",
            Theme.PREMIUM,
            f"Admin: {interaction.user.display_name} │ Mack Bot 2027"
        )

        # Use the group-agnostic admin panel
        view = AdminPanelQuickView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ─────────────── CONFIG COMMANDS ───────────────

    # ── Toggle settings that accept enable/disable ──
    TOGGLE_SETTINGS = {
        "auto_group_generation", "auto_registration_open", "midnight_reset",
        "match_reminders", "waiting_list", "team_memory",
    }
    # ── String settings (no integer parsing) ──
    STRING_SETTINGS = {
        "default_category_name", "event_name", "event_mode",
        "match_format", "group_naming_pattern",
    }
    # ── Human-readable descriptions for each toggle ──
    TOGGLE_DESCRIPTIONS = {
        "auto_group_generation": "Auto-create groups at midnight",
        "auto_registration_open": "Auto-open registration at scheduled time",
        "midnight_reset": "Run full midnight cleanup & reset cycle",
        "match_reminders": "Send match reminder DMs to teams",
        "waiting_list": "Enable waiting list when groups are full",
        "team_memory": "Remember team profiles for 30 days",
    }

    @app_commands.command(name="config", description="[Admin] Configure bot channels and settings")
    @app_commands.describe(
        setting="What to configure",
        channel="Channel to set (for channel settings)",
        value="Value to set (for non-channel settings — use enable/disable for toggles)"
    )
    @app_commands.choices(setting=[
        # ── Channel settings ──
        app_commands.Choice(name="📢 register_channel", value="register"),
        app_commands.Choice(name="📢 admin_channel", value="admin"),
        app_commands.Choice(name="📢 admin_log_channel", value="admin_log"),
        app_commands.Choice(name="📢 leaderboard_channel", value="leaderboard"),
        app_commands.Choice(name="📢 registered_teams_channel", value="registered_teams"),
        # ── Toggle settings (enable/disable) ──
        app_commands.Choice(name="🔘 auto_group_generation", value="auto_group_generation"),
        app_commands.Choice(name="🔘 auto_registration_open", value="auto_registration_open"),
        app_commands.Choice(name="🔘 midnight_reset", value="midnight_reset"),
        app_commands.Choice(name="🔘 match_reminders", value="match_reminders"),
        app_commands.Choice(name="🔘 waiting_list", value="waiting_list"),
        app_commands.Choice(name="🔘 team_memory", value="team_memory"),
        # ── Numeric settings ──
        app_commands.Choice(name="🔢 default_group_count", value="default_group_count"),
        app_commands.Choice(name="🔢 default_group_capacity", value="default_group_capacity"),
        app_commands.Choice(name="🔢 reminder_lead_minutes", value="reminder_lead_minutes"),
        app_commands.Choice(name="🔢 lock_minutes", value="lock_minutes"),
        app_commands.Choice(name="🔢 registration_open_hour", value="registration_open_hour"),
        app_commands.Choice(name="🔢 registration_open_minute", value="registration_open_minute"),
        app_commands.Choice(name="🔢 default_reserved_slots", value="default_reserved_slots"),
        # ── String settings ──
        app_commands.Choice(name="✏️ default_category_name", value="default_category_name"),
        app_commands.Choice(name="✏️ event_name", value="event_name"),
        app_commands.Choice(name="✏️ event_mode", value="event_mode"),
        app_commands.Choice(name="✏️ match_format", value="match_format"),
        app_commands.Choice(name="✏️ group_naming_pattern", value="group_naming_pattern"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_cmd(
        self,
        interaction: discord.Interaction,
        setting: str,
        channel: discord.TextChannel = None,
        value: str = None
    ):
        # ── Channel settings ──
        if setting in ("register", "admin", "admin_log", "leaderboard", "registered_teams"):
            if not channel:
                current = get_channel_config(setting)
                if current:
                    await interaction.response.send_message(
                        embed=make_embed("📋 Current Config", f"**{setting}_channel:** <#{current}>", Theme.INFO),
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        embed=make_embed(
                            "📋 Not Set",
                            f"**{setting}_channel** is not configured.\nUse `/config {setting} #channel` to set it.",
                            Theme.WARNING
                        ),
                        ephemeral=True
                    )
                return

            set_channel_config(setting, channel.id)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}_channel** set to {channel.mention}"),
                ephemeral=True
            )
            return

        # ── Toggle settings (enable/disable) ──
        if setting in self.TOGGLE_SETTINGS:
            desc = self.TOGGLE_DESCRIPTIONS.get(setting, setting)
            if not value:
                current = get_config(setting, True)
                status = "🟢 Enabled" if current else "🔴 Disabled"
                await interaction.response.send_message(
                    embed=make_embed(
                        f"🔘 {setting}",
                        f"**{desc}**\n\n"
                        f"**Current Status:** {status}\n\n"
                        f"{Theme.THIN_SEP}\n"
                        f"Use `/config {setting} value:enable` or `/config {setting} value:disable` to toggle.",
                        Theme.INFO
                    ),
                    ephemeral=True
                )
                return

            lowered = value.strip().lower()
            if lowered in ("enable", "on", "true", "1", "yes"):
                set_config(setting, True)
                await interaction.response.send_message(
                    embed=success_embed("✅ Enabled", f"**{desc}** is now 🟢 **Enabled**"),
                    ephemeral=True
                )
            elif lowered in ("disable", "off", "false", "0", "no"):
                set_config(setting, False)
                await interaction.response.send_message(
                    embed=success_embed("✅ Disabled", f"**{desc}** is now 🔴 **Disabled**"),
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", f"Use `enable` or `disable` for toggle settings.\nExample: `/config {setting} value:enable`"),
                    ephemeral=True
                )
            return

        # ── String settings ──
        if setting in self.STRING_SETTINGS:
            if not value:
                current = get_config(setting, "Not set")
                await interaction.response.send_message(
                    embed=make_embed("📋 Current Config", f"**{setting}:** `{current}`", Theme.INFO),
                    ephemeral=True
                )
                return

            set_config(setting, value)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}** set to `{value}`"),
                ephemeral=True
            )
            return

        # ── Numeric settings ──
        if not value:
            current = get_config(setting, "Not set")
            await interaction.response.send_message(
                embed=make_embed("📋 Current Config", f"**{setting}:** `{current}`", Theme.INFO),
                ephemeral=True
            )
            return

        try:
            int_value = int(value)
            if setting == "registration_open_hour" and (int_value < 0 or int_value > 23):
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", "registration_open_hour must be between 0 and 23."),
                    ephemeral=True
                )
                return
            if setting == "registration_open_minute" and (int_value < 0 or int_value > 59):
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", "registration_open_minute must be between 0 and 59."),
                    ephemeral=True
                )
                return
            if setting == "default_reserved_slots" and (int_value < 0 or int_value > 3):
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid Value", "default_reserved_slots must be between 0 and 3."),
                    ephemeral=True
                )
                return

            set_config(setting, int_value)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}** set to `{int_value}`"),
                ephemeral=True
            )
        except ValueError:
            set_config(setting, value)
            await interaction.response.send_message(
                embed=success_embed("✅ Config Updated", f"**{setting}** set to `{value}`"),
                ephemeral=True
            )

    # ─────────────── VIEW CONFIG ───────────────

    @app_commands.command(name="viewconfig", description="[Admin] View all bot configuration")
    @app_commands.checks.has_permissions(administrator=True)
    async def view_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        event_id = get_today_event_id()
        guild = interaction.guild
        bot = interaction.client

        # Retrieve MongoDB client and config collection
        from database import bot_config as config_collection, _client
        all_configs = list(config_collection.find({}))
        db_configs = {cfg.get("key"): cfg.get("value") for cfg in all_configs}

        # Load values directly from config files, db, or runtime settings
        import config
        from config import (
            DEFAULT_GROUP_CAPACITY, DEFAULT_GROUP_COUNT, DEFAULT_RESERVED_SLOTS,
            DEFAULT_CATEGORY_NAME, TIMEZONE_OFFSET, REGISTRATION_OPEN_HOUR, REGISTRATION_OPEN_MINUTE,
            DEFAULT_LOCK_MINUTES, DEFAULT_REMINDER_LEAD_MINUTES, load_schedule
        )
        from cogs.registration import is_registration_open

        # Local helper to safely add a split field (protects against >1024 char limits)
        def add_split_field(embed, name, value, inline=False):
            if len(value) <= 1000:
                embed.add_field(name=name, value=value, inline=inline)
                return
            lines = value.split("\n")
            current_chunk = []
            chunk_idx = 1
            for line in lines:
                if len("\n".join(current_chunk) + "\n" + line) > 1000:
                    embed.add_field(
                        name=f"{name} (Part {chunk_idx})" if chunk_idx > 1 or len(lines) > 1 else name,
                        value="\n".join(current_chunk),
                        inline=inline
                    )
                    current_chunk = [line]
                    chunk_idx += 1
                else:
                    current_chunk.append(line)
            if current_chunk:
                embed.add_field(
                    name=f"{name} (Part {chunk_idx})" if chunk_idx > 1 else name,
                    value="\n".join(current_chunk),
                    inline=inline
                )

        embeds = []

        # 1. 🏆 Event Information
        event_name = db_configs.get("event_name") or db_configs.get("default_category_name") or DEFAULT_CATEGORY_NAME
        event_mode = db_configs.get("event_mode", "Esports Scrims / Qualifiers")
        
        is_open, open_h, open_m, current_t = await is_registration_open()
        reg_status = "🟢 Open / Accepting Registrations" if is_open else "🔴 Closed / Locked"
        
        # 12-hour format for open time
        ampm = "PM" if open_h >= 12 else "AM"
        display_h = open_h if 0 < open_h <= 12 else (open_h - 12 if open_h > 12 else 12)
        open_time_str = f"{display_h:02d}:{open_m:02d} {ampm} IST"
        
        lock_minutes = db_configs.get("lock_minutes", DEFAULT_LOCK_MINUTES)
        close_time_desc = f"Auto-Locks {lock_minutes} minutes before each group match start"
        timezone_desc = f"IST (UTC+{TIMEZONE_OFFSET})"
        
        emb1 = make_embed(title="🏆 Event Information", color=Theme.TEAL)
        emb1.description = (
            f"◆ **Event Name:** `{event_name}`\n"
            f"◆ **Event Mode:** `{event_mode}`\n"
            f"◆ **Registration Status:** {reg_status}\n"
            f"◆ **Registration Open Time:** `{open_time_str}`\n"
            f"◆ **Registration Close Time:** `{close_time_desc}`\n"
            f"◆ **Event Time Zone:** `{timezone_desc}`"
        )
        embeds.append(emb1)

        # 2. 👥 Registration Details
        all_groups = group_model.get_all_groups(event_id)
        default_capacity = db_configs.get("default_group_capacity", DEFAULT_GROUP_CAPACITY)
        default_reserved = db_configs.get("default_reserved_slots", DEFAULT_RESERVED_SLOTS)
        
        total_registered = reg_model.count_registrations(event_id)
        total_capacity = sum(g.get("capacity", default_capacity) for g in all_groups)
        
        if len(all_groups) == 0:
            fallback_groups_count = db_configs.get("default_group_count", DEFAULT_GROUP_COUNT)
            total_capacity = fallback_groups_count * default_capacity
            remaining_slots = total_capacity
        else:
            remaining_slots = max(0, total_capacity - total_registered)
            
        waiting_list_status = db_configs.get("waiting_list_status", "Disabled")
        
        is_locked_all = all(g.get("locked") for g in all_groups) if all_groups else not is_open
        lock_status = "🔒 Locked" if is_locked_all else "🔓 Unlocked"
        
        emb2 = make_embed(title="👥 Registration Summary", color=Theme.INFO)
        emb2.description = (
            f"◆ **Capacity Per Group:** `{default_capacity}`\n"
            f"◆ **Reserved Slots:** `{default_reserved}`\n"
            f"◆ **Total Registered Teams:** `{total_registered}`\n"
            f"◆ **Remaining Slots:** `{remaining_slots}`\n"
            f"◆ **Total Capacity:** `{total_capacity}`\n"
            f"◆ **Waiting List Status:** `{waiting_list_status}`\n"
            f"◆ **Registration Lock Status:** `{lock_status}`"
        )
        embeds.append(emb2)

        # 3. 📂 Group Information & Feature Toggles
        # Read toggle states from MongoDB (default: True = enabled)
        toggle_auto_group = db_configs.get("auto_group_generation", True)
        toggle_auto_reg = db_configs.get("auto_registration_open", True)
        toggle_midnight = db_configs.get("midnight_reset", True)
        toggle_reminders = db_configs.get("match_reminders", True)
        toggle_waiting = db_configs.get("waiting_list", False)
        toggle_memory = db_configs.get("team_memory", True)

        def toggle_icon(val):
            return "🟢 Enabled" if val else "🔴 Disabled"
        
        total_groups = len(all_groups)
        active_groups = sum(1 for g in all_groups if not g.get("archived"))
        empty_groups = sum(1 for g in all_groups if g.get("current_count", 0) == g.get("reserved_slots", 0))
        full_groups = sum(1 for g in all_groups if g.get("current_count", 0) >= g.get("capacity", default_capacity))
        
        group_naming_pattern = db_configs.get("group_naming_pattern", "G{index:04d}")
        try:
            next_group_id = group_naming_pattern.format(index=total_groups + 1)
        except Exception:
            next_group_id = f"G{total_groups + 1:04d}"
            
        emb3 = make_embed(title="📂 Group & Autopilot Configuration", color=Theme.GOLD)
        emb3.description = (
            f"**── Feature Toggles ──**\n"
            f"◆ **Auto Group Generation:** {toggle_icon(toggle_auto_group)}\n"
            f"◆ **Auto Registration Open:** {toggle_icon(toggle_auto_reg)}\n"
            f"◆ **Midnight Reset:** {toggle_icon(toggle_midnight)}\n"
            f"◆ **Match Reminders:** {toggle_icon(toggle_reminders)}\n"
            f"◆ **Waiting List:** {toggle_icon(toggle_waiting)}\n"
            f"◆ **Team Memory (30-day):** {toggle_icon(toggle_memory)}\n\n"
            f"**── Group Stats ──**\n"
            f"◆ **Total Groups Generated:** `{total_groups}`\n"
            f"◆ **Active Groups:** `{active_groups}`\n"
            f"◆ **Empty Groups:** `{empty_groups}`\n"
            f"◆ **Full Groups:** `{full_groups}`\n"
            f"◆ **Capacity Per Group:** `{default_capacity}`\n"
            f"◆ **Group Naming Pattern:** `{group_naming_pattern}`\n"
            f"◆ **Next Group ID:** `{next_group_id}`"
        )
        embeds.append(emb3)

        # 4. 📂 Generated Groups List
        group_lines = []
        for g in all_groups:
            gid = g.get("group_id")
            count = g.get("current_count", 0)
            cap = g.get("capacity", default_capacity)
            reserved = g.get("reserved_slots", 0)
            pub_count = max(0, count - reserved)
            pub_cap = cap - reserved
            group_lines.append(f"◆ **{gid}** • {pub_count}/{pub_cap}")
            
        if not group_lines:
            group_lines.append("*No groups currently generated for today.*")
            
        groups_chunk_size = 20
        for idx in range(0, len(group_lines), groups_chunk_size):
            chunk = group_lines[idx:idx + groups_chunk_size]
            emb_groups = make_embed(
                title=f"📂 Generated Groups" if len(group_lines) <= groups_chunk_size else f"📂 Generated Groups (Page {idx//groups_chunk_size + 1})",
                desc="\n".join(chunk),
                color=Theme.INFO
            )
            embeds.append(emb_groups)

        # 5. 🗺 Match Configuration
        m1_maps = set()
        m1_times = set()
        m2_maps = set()
        m2_times = set()
        
        for g in all_groups:
            m1 = g.get("match1", {})
            m2 = g.get("match2", {})
            if m1.get("map") and m1.get("map") != "TBD":
                m1_maps.add(m1.get("map"))
            if m1.get("start") and m1.get("start") != "TBD":
                m1_times.add(m1.get("start"))
            if m2.get("map") and m2.get("map") != "TBD":
                m2_maps.add(m2.get("map"))
            if m2.get("start") and m2.get("start") != "TBD":
                m2_times.add(m2.get("start"))
                
        if not m1_maps or not m2_maps:
            schedule = load_schedule()
            for s in schedule:
                m1 = s.get("match1", {})
                m2 = s.get("match2", {})
                if m1.get("map") and m1.get("map") != "TBD":
                    m1_maps.add(m1.get("map"))
                if m1.get("start") and m1.get("start") != "TBD":
                    m1_times.add(m1.get("start"))
                if m2.get("map") and m2.get("map") != "TBD":
                    m2_maps.add(m2.get("map"))
                if m2.get("start") and m2.get("start") != "TBD":
                    m2_times.add(m2.get("start"))
                    
        m1_map_str = ", ".join(sorted(m1_maps)) if m1_maps else "TBD"
        m2_map_str = ", ".join(sorted(m2_maps)) if m2_maps else "TBD"
        
        def format_time_range(times_set):
            if not times_set:
                return "TBD"
            times_list = sorted(list(times_set))
            if len(times_list) == 1:
                return times_list[0]
            return f"{times_list[0]} - {times_list[-1]} (Varies)"
            
        m1_time_str = format_time_range(m1_times)
        m2_time_str = format_time_range(m2_times)
        
        match_format = db_configs.get("match_format", "Squad TPP")
        match_count = db_configs.get("match_count", 2)
        
        emb_match = make_embed(title="🗺 Match Configuration", color=Theme.ORANGE)
        emb_match.description = (
            f"◆ **Match 1 Map:** `{m1_map_str}`\n"
            f"◆ **Match 1 Time:** `{m1_time_str}`\n"
            f"◆ **Match 2 Map:** `{m2_map_str}`\n"
            f"◆ **Match 2 Time:** `{m2_time_str}`\n"
            f"◆ **Match Format:** `{match_format}`\n"
            f"◆ **Match Count:** `{match_count}`"
        )
        embeds.append(emb_match)

        # 6. 📢 Discord Configuration
        reg_channel_id = get_channel_config("register")
        announcement_channel_id = db_configs.get("channel_announcement") or db_configs.get("channel_announcements")
        if not announcement_channel_id:
            ann_ch = discord.utils.get(guild.text_channels, name="announcements") or discord.utils.get(guild.text_channels, name="announcement")
            if ann_ch:
                announcement_channel_id = ann_ch.id
                
        result_channel_id = get_channel_config("leaderboard")
        admin_channel_id = get_channel_config("admin")
        
        reg_ch_mention = f"<#{reg_channel_id}> (`{reg_channel_id}`)" if reg_channel_id else "`Not set`"
        ann_ch_mention = f"<#{announcement_channel_id}> (`{announcement_channel_id}`)" if announcement_channel_id else "`Not set`"
        res_ch_mention = f"<#{result_channel_id}> (`{result_channel_id}`)" if result_channel_id else "`Not set`"
        admin_ch_mention = f"<#{admin_channel_id}> (`{admin_channel_id}`)" if admin_channel_id else "`Not set`"
        
        # Category IDs
        category_set = set()
        for k, v in db_configs.items():
            if k.startswith("category_") and isinstance(v, int):
                category_set.add(v)
        for g in all_groups:
            cat_id = g.get("category_id")
            if cat_id:
                category_set.add(cat_id)
                
        category_ids_str = ", ".join(f"<#{cid}> (`{cid}`)" for cid in sorted(category_set)) if category_set else "None"
        
        # Role IDs
        role_mentions = []
        role_set = set()
        for g in all_groups:
            rid = g.get("role_id")
            gid = g.get("group_id")
            if rid:
                role_mentions.append(f"Group {gid}: <@&{rid}> (`{rid}`)")
                role_set.add(rid)
        for k, v in db_configs.items():
            if (k.endswith("_role") or k.startswith("role_")) and isinstance(v, int) and v not in role_set:
                role_mentions.append(f"**{k}:** <@&{v}> (`{v}`)")
                role_set.add(v)
                
        role_mentions_str = "\n".join(role_mentions) if role_mentions else "No active roles configured."
        
        emb_discord = make_embed(title="📢 Discord Configuration", color=Theme.PREMIUM)
        emb_discord.description = (
            f"◆ **Registration Channel:** {reg_ch_mention}\n"
            f"◆ **Announcement Channel:** {ann_ch_mention}\n"
            f"◆ **Result Channel:** {res_ch_mention}\n"
            f"◆ **Admin Channel:** {admin_ch_mention}\n"
            f"◆ **Category IDs:** {category_ids_str}"
        )
        
        add_split_field(emb_discord, "Role IDs", role_mentions_str, inline=False)
        embeds.append(emb_discord)

        # 7. ⚙️ Bot Configuration
        try:
            _client.admin.command("ping")
            db_status = "🟢 Connected"
        except Exception:
            db_status = "🔴 Disconnected / Error"
            
        from config import MONGO_URI
        masked_mongo_uri = "`Hidden / Masked`"
        if MONGO_URI:
            import re
            masked_mongo_uri = re.sub(r'mongodb(\+srv)?://([^:]+):([^@]+)@', r'mongodb\1://***:***@', MONGO_URI)
            
        bot_status = "🟢 Online"
        bot_latency = f"{bot.latency * 1000:.0f} ms"
        bot_version = db_configs.get("version", "2.0.0 (Esports Edition)")
        bot_prefix = db_configs.get("prefix", "!")
        total_slash_commands = len(bot.tree.get_commands())
        
        reminders_cog = bot.get_cog("RemindersCog")
        scheduler_status = "Active" if reminders_cog and reminders_cog.reminder_loop.is_running() else "Inactive"
        
        emb_bot = make_embed(title="⚙️ Bot Configuration", color=Theme.DARK)
        emb_bot.description = (
            f"◆ **Database Status:** {db_status}\n"
            f"◆ **MongoDB Connection:** `{masked_mongo_uri}`\n"
            f"◆ **Bot Status:** {bot_status} (Latency: `{bot_latency}`)\n"
            f"◆ **Version:** `{bot_version}`\n"
            f"◆ **Prefix:** `{bot_prefix}`\n"
            f"◆ **Slash Commands:** `{total_slash_commands}` synced\n"
            f"◆ **Scheduler Status:** `{scheduler_status}`"
        )
        embeds.append(emb_bot)

        # 8. Miscellaneous Configs (Dynamically fetch all other config values in DB and config.py)
        displayed_db_keys = {
            "event_name", "event_mode", "default_group_capacity", "default_reserved_slots",
            "waiting_list_status", "default_group_count", "group_naming_pattern",
            "match_format", "match_count", "channel_register", "channel_admin",
            "channel_admin_log", "channel_leaderboard", "channel_registered_teams",
            "version", "prefix", "schedule", "registration_open_hour", "registration_open_minute",
            "lock_minutes", "reminder_lead_minutes", "token", "mongo_uri"
        }
        for k in db_configs.keys():
            if k.startswith("category_") or k.startswith("channel_") or k.startswith("role_"):
                displayed_db_keys.add(k)
                
        import inspect
        config_configs = {}
        for name, val in inspect.getmembers(config):
            if name.isupper() and not inspect.ismodule(val) and not inspect.isroutine(val):
                if name in ("TOKEN", "MONGO_URI"):
                    continue
                displayed_config_names = {
                    "DEFAULT_GROUP_CAPACITY", "DEFAULT_GROUP_COUNT", "DEFAULT_RESERVED_SLOTS",
                    "DEFAULT_REMINDER_LEAD_MINUTES", "DEFAULT_LOCK_MINUTES", "REGISTRATION_OPEN_HOUR",
                    "REGISTRATION_OPEN_MINUTE", "DEFAULT_CATEGORY_NAME", "BOT_PREFIX", "TIMEZONE_OFFSET"
                }
                if name not in displayed_config_names:
                    config_configs[name] = val
                    
        other_configs = []
        for k, v in db_configs.items():
            if k not in displayed_db_keys:
                if k == "position_points":
                    if isinstance(v, dict):
                        pts_summary = ", ".join(f"#{rank}: {pts}pt" for rank, pts in sorted(v.items(), key=lambda x: int(x[0])) if pts > 0)
                        other_configs.append(f"  ◆ **position_points:** `{pts_summary or 'No points configured'}`")
                    else:
                        other_configs.append(f"  ◆ **position_points:** `{v}`")
                elif k == "registration_open_subscribers":
                    sub_count = len(v) if isinstance(v, list) else 0
                    other_configs.append(f"  ◆ **notify_subscribers:** `{sub_count} users`")
                elif k in ("token", "mongo_uri"):
                    other_configs.append(f"  ◆ **{k}:** `*Hidden*`")
                else:
                    other_configs.append(f"  ◆ **{k}:** `{v}`")
                    
        for k, v in config_configs.items():
            if k.lower() in db_configs or k in db_configs:
                continue
            if k == "DEFAULT_POSITION_POINTS":
                if isinstance(v, dict):
                    pts_summary = ", ".join(f"#{rank}: {pts}pt" for rank, pts in sorted(v.items(), key=lambda x: int(x[0])) if pts > 0)
                    other_configs.append(f"  ◆ **DEFAULT_POSITION_POINTS:** `{pts_summary}`")
                else:
                    other_configs.append(f"  ◆ **DEFAULT_POSITION_POINTS:** `{v}`")
            elif k == "RANK_EMOJIS":
                other_configs.append(f"  ◆ **RANK_EMOJIS:** `{len(v)} emojis`")
            else:
                other_configs.append(f"  ◆ **{k}:** `{v}`")
                
        # Split other_configs across multiple embeds if description grows too long
        if other_configs:
            other_chunk = []
            char_count = 0
            other_page = 1
            
            for line in other_configs:
                if char_count + len(line) + 1 > 3800:
                    emb_other = make_embed(
                        title=f"⚙️ Miscellaneous Configs (Page {other_page})",
                        desc="\n".join(other_chunk),
                        color=Theme.ORANGE
                    )
                    embeds.append(emb_other)
                    other_chunk = [line]
                    char_count = len(line)
                    other_page += 1
                else:
                    other_chunk.append(line)
                    char_count += len(line) + 1
                    
            if other_chunk:
                emb_other = make_embed(
                    title=f"⚙️ Miscellaneous Configs (Page {other_page})" if other_page > 1 else "⚙️ Miscellaneous Configs",
                    desc="\n".join(other_chunk),
                    color=Theme.ORANGE
                )
                embeds.append(emb_other)

        # Format footer for page indexing on all embeds and send
        total_embeds = len(embeds)
        for idx, emb in enumerate(embeds):
            emb.set_footer(text=f"Embed {idx+1}/{total_embeds} │ Mack Bot Configuration Overview")

        # Send embeds in chunks of 10
        for i in range(0, len(embeds), 10):
            chunk = embeds[i:i + 10]
            await interaction.followup.send(embeds=chunk, ephemeral=True)

    # ─────────────── UNBAN COMMAND ───────────────

    @app_commands.command(name="unban", description="[Admin] Unban a player")
    @app_commands.describe(member="The member to unban")
    @app_commands.checks.has_permissions(administrator=True)
    async def unban_cmd(self, interaction: discord.Interaction, member: discord.Member):
        owner_id = str(member.id)
        success = punishment.unban_user(owner_id)

        if not success:
            await interaction.response.send_message(
                embed=error_embed("⚠️ Not Banned", f"{member.mention} is not currently banned."),
                ephemeral=True
            )
            return

        embed = make_embed(
            "🔓 Player Unbanned",
            f"{Theme.SEP}\n\n"
            f"👤 **Player:** {member.mention}\n"
            f"**Unbanned by:** {interaction.user.mention}\n\n{Theme.SEP}",
            Theme.SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        log_ch_id = get_channel_config("admin_log")
        if log_ch_id:
            log_ch = interaction.guild.get_channel(log_ch_id)
            if log_ch:
                await log_ch.send(embed=embed)

        try:
            await member.send(embed=make_embed(
                "🔓 You've Been Unbanned",
                f"You can now register for scrims again.",
                Theme.SUCCESS
            ))
        except: pass

    # ─────────────── BAN LIST ───────────────

    @app_commands.command(name="banlist", description="[Admin] View all active bans")
    @app_commands.checks.has_permissions(administrator=True)
    async def banlist_cmd(self, interaction: discord.Interaction):
        bans = punishment.get_active_bans()

        if not bans:
            await interaction.response.send_message(
                embed=success_embed("✅ No Bans", "No players are currently banned."),
                ephemeral=True
            )
            return

        lines = []
        for ban in bans:
            uid = ban.get("owner_id", "?")
            reason = ban.get("reason", "No reason")
            exp = ban.get("expires_at", "?")
            if exp == "never":
                exp_display = "Permanent"
            else:
                try:
                    exp_dt = datetime.datetime.fromisoformat(exp) + datetime.timedelta(hours=TIMEZONE_OFFSET)
                    exp_display = exp_dt.strftime("%Y-%m-%d %I:%M %p")
                except:
                    exp_display = exp
            lines.append(f"• <@{uid}> — `{reason}`\n  └ Expires: `{exp_display}`")

        embed = make_embed(
            "🔨 Active Bans",
            f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.ERROR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────── /reserve COMMAND GROUP ───────────────

    reserve_group = app_commands.Group(name="reserve", description="[Admin] Manage reserved slots")

    @reserve_group.command(name="slots", description="Set the number of reserved slots per group (0-3)")
    @app_commands.describe(count="Number of reserved slots (0 to 3)")
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_slots(self, interaction: discord.Interaction, count: int):
        if count < 0 or count > 3:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Count", "Reserved slots must be between 0 and 3."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Save as default for future provisions
        await asyncio.to_thread(set_config, "default_reserved_slots", count)

        # Update all groups for today's event
        event_id = get_today_event_id()
        updated_count = await asyncio.to_thread(group_model.set_all_reserved_slots, event_id, count)

        # Refresh all rosters and registers
        guild = interaction.guild
        all_groups = await asyncio.to_thread(group_model.get_all_groups, event_id)
        for g in all_groups:
            await update_group_roster(guild, event_id, g["group_id"])

        # Update slot availability board
        await update_registration_board(guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Reserved Slots Updated",
                f"{Theme.SEP}\n\n"
                f"Default reserved slots count set to: **{count}**\n"
                f"Updated **{updated_count}** active groups for today.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @reserve_group.command(name="fill", description="Fill a reserved slot in a group with a team name")
    @app_commands.describe(
        group_id="Group ID (e.g. G0001)",
        slot="Roster slot number (1 to reserved count)",
        team_name="Name of the team to assign"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_fill(self, interaction: discord.Interaction, group_id: str, slot: int, team_name: str):
        event_id = get_today_event_id()
        group_doc = await asyncio.to_thread(group_model.get_group, event_id, group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{group_id}` not found for today."),
                ephemeral=True
            )
            return

        reserved = group_doc.get("reserved_slots", 0)
        if slot < 1 or slot > reserved:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Slot", f"Group `{group_id}` only has `{reserved}` reserved slots (1-{reserved})."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        updated_group = await asyncio.to_thread(group_model.fill_reserved_slot, event_id, group_id, slot, team_name)
        if not updated_group:
            await interaction.followup.send(
                embed=error_embed("❌ Error", "Could not fill reserved slot."),
                ephemeral=True
            )
            return

        # Refresh roster in the group channel and board
        await update_group_roster(interaction.guild, event_id, group_id)
        await update_registration_board(interaction.guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Filled",
                f"{Theme.SEP}\n\n"
                f"Filled Slot **{slot:02d}** in **{group_id}** with team **{team_name}**.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @reserve_group.command(name="clear", description="Clear a filled reserved slot back to empty RESERVED status")
    @app_commands.describe(
        group_id="Group ID (e.g. G0001)",
        slot="Roster slot number to clear"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_clear(self, interaction: discord.Interaction, group_id: str, slot: int):
        event_id = get_today_event_id()
        group_doc = await asyncio.to_thread(group_model.get_group, event_id, group_id)
        if not group_doc:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Group `{group_id}` not found for today."),
                ephemeral=True
            )
            return

        reserved = group_doc.get("reserved_slots", 0)
        if slot < 1 or slot > reserved:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid Slot", f"Group `{group_id}` only has `{reserved}` reserved slots (1-{reserved})."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        updated_group = await asyncio.to_thread(group_model.clear_reserved_slot, event_id, group_id, slot)
        if not updated_group:
            await interaction.followup.send(
                embed=error_embed("❌ Error", "Could not clear reserved slot."),
                ephemeral=True
            )
            return

        # Refresh roster in the group channel and board
        await update_group_roster(interaction.guild, event_id, group_id)
        await update_registration_board(interaction.guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Slot Cleared",
                f"{Theme.SEP}\n\n"
                f"Cleared Slot **{slot:02d}** in **{group_id}** back to reserved status.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    @reserve_group.command(name="view", description="View reserved slots configuration and status")
    @app_commands.checks.has_permissions(administrator=True)
    async def reserve_view(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        all_groups = await asyncio.to_thread(group_model.get_all_groups, event_id)
        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups found for today."),
                ephemeral=True
            )
            return

        lines = []
        for g in all_groups:
            gid = g["group_id"]
            res_count = g.get("reserved_slots", 0)
            res_teams = g.get("reserved_teams", {})
            filled_lines = []
            for s in range(1, res_count + 1):
                tname = res_teams.get(str(s), "🔴 *Empty*")
                filled_lines.append(f"  └ Slot {s:02d}: **{tname}**")
            
            lines.append(f"**✦ Group {gid}** ({res_count} reserved slots)")
            if filled_lines:
                lines.extend(filled_lines)
            else:
                lines.append("  └ *None*")

        embed = make_embed(
            "📋 Reserved Slots Status",
            f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.INFO
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ═══════════════════ ADMIN PANEL QUICK VIEW ═══════════════════

class AdminPanelQuickView(ui.View):
    """Quick admin panel from /panel command (non-persistent)."""

    def __init__(self):
        super().__init__(timeout=300)

    @ui.button(label="🔔 Match Reminder", style=discord.ButtonStyle.primary, row=0)
    async def reminder_btn(self, interaction: discord.Interaction, button: ui.Button):
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)

        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups provisioned for today."),
                ephemeral=True
            )
            return

        options = []
        for g in all_groups[:25]:
            gid = g["group_id"]
            sent = "✅ Sent" if g.get("reminder_sent") else "⏳ Pending"
            options.append(discord.SelectOption(
                label=f"Group {gid} — {sent}",
                value=gid, emoji="🔔"
            ))

        view = ui.View(timeout=60)
        select = ReminderGroupSelect(event_id, options)
        view.add_item(select)

        await interaction.response.send_message(
            embed=make_embed("🔔 Select Group", "Choose which group to send a reminder to.", Theme.ACCENT),
            view=view, ephemeral=True
        )

    @ui.button(label="📋 Publish Slot List", style=discord.ButtonStyle.secondary, row=0)
    async def slot_list_btn(self, interaction: discord.Interaction, button: ui.Button):
        event_id = get_today_event_id()
        all_groups = group_model.get_all_groups(event_id)

        if not all_groups:
            await interaction.response.send_message(
                embed=error_embed("❌ No Groups", "No groups for today."),
                ephemeral=True
            )
            return

        options = [discord.SelectOption(label=f"Group {g['group_id']}", value=g["group_id"], emoji="📋")
                   for g in all_groups[:25]]

        view = ui.View(timeout=60)
        select = SlotListGroupSelect(event_id, options)
        view.add_item(select)

        await interaction.response.send_message(
            embed=make_embed("📋 Select Group", "Choose which group's slot list to publish.", Theme.ACCENT),
            view=view, ephemeral=True
        )

    @ui.button(label="🔧 Manage Matches", style=discord.ButtonStyle.secondary, row=1)
    async def manage_btn(self, interaction: discord.Interaction, button: ui.Button):
        view = AdminManageSubView(get_today_event_id(), None)
        await interaction.response.send_message(
            embed=make_embed(
                "🔧 Manage Matches",
                f"{Theme.SEP}\n\n"
                f"**✏️ Edit Match** — Change IDP, start time, or map for a group\n"
                f"**🔀 Move Team** — Admin override to move a team between groups\n\n{Theme.SEP}",
                Theme.ACCENT
            ),
            view=view, ephemeral=True
        )

    @ui.button(label="🔨 Punish Team", style=discord.ButtonStyle.danger, row=1)
    async def punish_btn(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PunishModal())

    @ui.button(label="🏆 Qualified Teams", style=discord.ButtonStyle.success, row=1)
    async def qualified_btn(self, interaction: discord.Interaction, button: ui.Button):
        event_id = get_today_event_id()
        from database import match_results as results_collection
        results = list(results_collection.find({"event_id": event_id}))

        if not results:
            await interaction.response.send_message(
                embed=error_embed("❌ No Standings", "No match results recorded yet today."),
                ephemeral=True
            )
            return

        team_totals = {}
        for r in results:
            tk = r.get("team_key") or r.get("team_name", "").strip().lower()
            if not tk:
                continue
            if tk not in team_totals:
                team_totals[tk] = {"team_name": r.get("team_name", "?"), "total_kills": 0, "total_points": 0}
            team_totals[tk]["total_kills"] += r.get("kills", 0)
            team_totals[tk]["total_points"] += r.get("total_points", 0)

        sorted_teams = sorted(team_totals.values(), key=lambda x: (x["total_points"], x["total_kills"]), reverse=True)
        lines = [f"{get_rank_emoji(i+1)} **{t['team_name']}** ─ `{t['total_points']}` pts │ 💀 `{t['total_kills']}`"
                 for i, t in enumerate(sorted_teams[:16])]

        await interaction.response.send_message(
            embed=make_embed("🏆 Top Qualifying Teams",
                             f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}", Theme.GOLD),
            ephemeral=True
        )


class ReminderGroupSelect(ui.Select):
    def __init__(self, event_id, options):
        self.event_id = event_id
        super().__init__(placeholder="🔔 Select group…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        gid = self.values[0]
        cog = interaction.client.get_cog("RemindersCog")
        if cog:
            await cog.remind_group.callback(cog, interaction, group_id=gid)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded."),
                ephemeral=True
            )


class SlotListGroupSelect(ui.Select):
    def __init__(self, event_id, options):
        self.event_id = event_id
        super().__init__(placeholder="📋 Select group…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        gid = self.values[0]
        cog = interaction.client.get_cog("RemindersCog")
        if cog:
            await cog.publish_slot_list.callback(cog, interaction, group_id=gid)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Error", "Reminders cog not loaded."),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(AdminPanelCog(bot))
