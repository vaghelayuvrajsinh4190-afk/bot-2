"""
Mack Bot — Provisioning Cog
Handles the full autopilot system:
  - Midnight Reset: cleanup, board reset, lock registration, auto-provision
  - Registration Open: 10 AM IST unlock
  - Manual /provision, /addgroups, /deprovision, /set_groups, /update_time
"""

import datetime
import asyncio
import json
import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import (
    Theme, TIMEZONE_OFFSET,
    DEFAULT_GROUP_CAPACITY, DEFAULT_GROUP_COUNT,
    REGISTRATION_OPEN_HOUR, REGISTRATION_OPEN_MINUTE,
    load_schedule, save_schedule, get_schedule_for_group
)
from utils.embeds import (
    make_embed, error_embed, success_embed,
    build_slot_availability_embed, build_registration_board_embed,
    build_group_control_panel_embed
)
from utils.permissions import (
    get_or_create_role, create_group_channel,
    create_day_category, cleanup_channel, cleanup_role, cleanup_category
)
from models import group as group_model, punishment
from models import team_profile
from database import get_config, set_config, get_channel_config


# ═══════════════════ HELPERS ═══════════════════

def get_today_event_id():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%Y-%m-%d")


def get_today_display():
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    return local_now.strftime("%d %B").upper()


def generate_group_id(index: int, event_id: str):
    """Generate a group ID like G0001, G0002, etc."""
    return f"G{index:04d}"


# ═══════════════════ PROVISIONING COG ═══════════════════

class ProvisioningCog(commands.Cog):
    """Handles daily group provisioning, autopilot, and nightly cleanup."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Start background tasks."""
        self.autopilot_loop.start()

    async def cog_unload(self):
        self.autopilot_loop.cancel()

    # ═══════════════════ AUTOPILOT LOOP ═══════════════════

    @tasks.loop(minutes=1)
    async def autopilot_loop(self):
        """
        Master autopilot loop — checks every minute for:
        1. Midnight Reset (00:00 IST)
        2. Registration Open (10:00 AM IST)
        """
        utc_now = datetime.datetime.utcnow()
        local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)

        if not self.bot.guilds:
            return

        guild = self.bot.guilds[0]

        # ─────── MIDNIGHT RESET (00:00 IST) ───────
        if local_now.hour == 0 and local_now.minute == 0:
            await self._midnight_reset(guild, local_now)

        # ─────── REGISTRATION OPEN (10:00 AM IST) ───────
        if local_now.hour == REGISTRATION_OPEN_HOUR and local_now.minute == REGISTRATION_OPEN_MINUTE:
            await self._registration_open(guild)

    @autopilot_loop.before_loop
    async def before_autopilot(self):
        await self.bot.wait_until_ready()

    # ═══════════════════ MIDNIGHT RESET ═══════════════════

    async def _midnight_reset(self, guild, local_now):
        """
        Full midnight reset cycle:
        1. Delete yesterday's group channels
        2. Hard data wipe (clear daily registrations)
        3. Reset permanent registration board to 0/21
        4. Lock registration button
        5. Create fresh group channels for new day
        6. Deploy control panels
        """
        print("🕛 MIDNIGHT RESET: Starting full reset cycle...", flush=True)

        # Get yesterday's event ID
        yesterday = (local_now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_groups = group_model.get_all_groups(yesterday, include_archived=True)

        # ── Step 1: Channel Cleanup ──
        if yesterday_groups:
            await self._cleanup_event(guild, yesterday, yesterday_groups)
            print(f"🧹 Cleaned up {len(yesterday_groups)} groups from {yesterday}", flush=True)

        # ── Step 2: Clean expired data ──
        expired_bans = punishment.cleanup_expired_bans()
        expired_profiles = team_profile.cleanup_expired_profiles()
        if expired_bans:
            print(f"🧹 Expired {expired_bans} bans", flush=True)
        if expired_profiles:
            print(f"🧹 Expired {expired_profiles} team profiles (30-day TTL)", flush=True)

        # ── Step 3: Reset Permanent Registration Board ──
        await self._reset_registration_board(guild)
        print("📋 Registration board reset to 0/0", flush=True)

        # ── Step 4: Lock Registration ──
        await self._lock_registration(guild)
        print("🔒 Registration locked", flush=True)

        # ── Step 5: Auto-Provision New Day ──
        event_id = get_today_event_id()
        count = int(get_config("default_group_count", DEFAULT_GROUP_COUNT))
        cap = int(get_config("default_group_capacity", DEFAULT_GROUP_CAPACITY))
        await self._auto_provision(guild, event_id, count, cap)
        print(f"📦 Auto-provisioned {count} groups for {event_id}", flush=True)

        # ── Log to admin channel ──
        log_channel_id = get_channel_config("admin_log")
        if log_channel_id:
            log_ch = guild.get_channel(log_channel_id)
            if log_ch:
                await log_ch.send(
                    embed=make_embed(
                        "🕛 Midnight Reset Complete",
                        f"{Theme.SEP}\n\n"
                        f"**Yesterday cleaned:** `{len(yesterday_groups)}` groups\n"
                        f"**Bans expired:** `{expired_bans}`\n"
                        f"**Profiles expired:** `{expired_profiles}`\n"
                        f"**New groups created:** `{count}`\n"
                        f"**Registration:** 🔒 Locked\n\n{Theme.SEP}",
                        Theme.SUCCESS
                    )
                )

        print("✅ Midnight reset complete.", flush=True)

    # ═══════════════════ REGISTRATION OPEN ═══════════════════

    async def _registration_open(self, guild):
        """Unlock registration at the configured time (default 10:00 AM IST)."""
        print(f"🕙 REGISTRATION OPEN: Unlocking registration...", flush=True)

        await self._unlock_registration(guild)

        log_channel_id = get_channel_config("admin_log")
        if log_channel_id:
            log_ch = guild.get_channel(log_channel_id)
            if log_ch:
                await log_ch.send(
                    embed=make_embed(
                        "🔓 Registration Open",
                        f"{Theme.SEP}\n\n"
                        f"📥 **Registration is now OPEN!**\n"
                        f"Players can now register for today's scrims.\n\n{Theme.SEP}",
                        Theme.SUCCESS
                    )
                )

        print("✅ Registration unlocked.", flush=True)

    # ═══════════════════ BOARD & BUTTON MANAGEMENT ═══════════════════

    async def _reset_registration_board(self, guild):
        """Reset the permanent registration board embed to 0/0 (empty)."""
        reg_channel_id = get_channel_config("register")
        if not reg_channel_id:
            return

        channel = guild.get_channel(reg_channel_id)
        if not channel:
            return

        slot_msg_id = get_config("slot_message_id")
        if not slot_msg_id:
            return

        try:
            msg = await channel.fetch_message(slot_msg_id)
            empty_embed = build_registration_board_embed(groups=None)
            await msg.edit(embed=empty_embed)
        except discord.NotFound:
            print("⚠️ Slot board message not found, will recreate on provision.", flush=True)
        except Exception as e:
            print(f"⚠️ Failed to reset board: {e}", flush=True)

    async def _lock_registration(self, guild):
        """Change the register button to disabled 🔒 Registration Closed."""
        reg_channel_id = get_channel_config("register")
        if not reg_channel_id:
            return

        channel = guild.get_channel(reg_channel_id)
        if not channel:
            return

        slot_msg_id = get_config("slot_message_id")
        if not slot_msg_id:
            return

        try:
            msg = await channel.fetch_message(slot_msg_id)
            from cogs.registration import PersistentRegisterView
            locked_view = PersistentRegisterView(locked=True)
            await msg.edit(view=locked_view)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ Failed to lock registration: {e}", flush=True)

    async def _unlock_registration(self, guild):
        """Change the register button back to active 📥 Register Team."""
        reg_channel_id = get_channel_config("register")
        if not reg_channel_id:
            return

        channel = guild.get_channel(reg_channel_id)
        if not channel:
            return

        slot_msg_id = get_config("slot_message_id")
        if not slot_msg_id:
            return

        try:
            msg = await channel.fetch_message(slot_msg_id)

            # Also refresh the board embed with current groups
            event_id = get_today_event_id()
            all_groups = group_model.get_all_groups(event_id)
            embed = build_registration_board_embed(all_groups)

            from cogs.registration import PersistentRegisterView
            unlocked_view = PersistentRegisterView(locked=False)
            await msg.edit(embed=embed, view=unlocked_view)
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"⚠️ Failed to unlock registration: {e}", flush=True)

    # ═══════════════════ AUTO-PROVISION ═══════════════════

    async def ensure_setup_channels(self, guild, event_id):
        """
        Ensure registration, logs, and receipt channels are created and configured.
        Deploys the registration board automatically if it doesn't exist.
        """
        from database import set_channel_config, set_config

        # 1. Ensure register channel
        reg_channel_id = get_channel_config("register")
        reg_channel = guild.get_channel(reg_channel_id) if reg_channel_id else None

        if not reg_channel:
            reg_channel = discord.utils.get(guild.text_channels, name="register-here")
            if not reg_channel:
                reg_channel = discord.utils.get(guild.text_channels, name="register")
            if not reg_channel:
                try:
                    reg_channel = await guild.create_text_channel(
                        name="register-here",
                        topic="📥 Register here for today's scrims!"
                    )
                except Exception as e:
                    print(f"❌ Failed to create register channel: {e}", flush=True)

            if reg_channel:
                set_channel_config("register", reg_channel.id)

        # Configure register channel permissions (disable sending messages for @everyone)
        if reg_channel:
            try:
                await reg_channel.set_permissions(
                    guild.default_role,
                    send_messages=False,
                    read_messages=True,
                    read_message_history=True
                )
                await reg_channel.set_permissions(
                    guild.me,
                    send_messages=True,
                    embed_links=True,
                    read_message_history=True,
                    manage_messages=True
                )
            except Exception as e:
                print(f"⚠️ Failed to set register channel permissions: {e}", flush=True)

        # 2. Ensure registered-teams channel
        teams_channel_id = get_channel_config("registered_teams")
        teams_channel = guild.get_channel(teams_channel_id) if teams_channel_id else None

        if not teams_channel:
            teams_channel = discord.utils.get(guild.text_channels, name="registered-teams")
            if not teams_channel:
                try:
                    teams_channel = await guild.create_text_channel(
                        name="registered-teams",
                        topic="📋 Live team registration receipts"
                    )
                except Exception as e:
                    print(f"❌ Failed to create registered-teams channel: {e}", flush=True)

            if teams_channel:
                set_channel_config("registered_teams", teams_channel.id)

        # Configure registered-teams permissions: read-only for public
        if teams_channel:
            try:
                await teams_channel.set_permissions(
                    guild.default_role,
                    send_messages=False,
                    read_messages=True,
                    read_message_history=True
                )
            except Exception as e:
                print(f"⚠️ Failed to set registered-teams permissions: {e}", flush=True)

        # 3. Ensure admin-log channel
        log_channel_id = get_channel_config("admin_log")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

        if not log_channel:
            log_channel = discord.utils.get(guild.text_channels, name="admin-log")
            if not log_channel:
                try:
                    overwrites = {
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
                    }
                    log_channel = await guild.create_text_channel(
                        name="admin-log",
                        topic="⚙️ Bot admin logs and audit trail",
                        overwrites=overwrites
                    )
                except Exception as e:
                    print(f"❌ Failed to create admin-log channel: {e}", flush=True)

            if log_channel:
                set_channel_config("admin_log", log_channel.id)

        # 4. Ensure registration board message is posted in the register channel
        if reg_channel:
            slot_msg_id = get_config("slot_message_id")
            board_msg = None
            if slot_msg_id:
                try:
                    board_msg = await reg_channel.fetch_message(slot_msg_id)
                except Exception:
                    board_msg = None

            if not board_msg:
                # Post the board
                all_groups = group_model.get_all_groups(event_id)
                embed = build_registration_board_embed(all_groups)
                from cogs.registration import PersistentRegisterView
                view = PersistentRegisterView(locked=False)
                try:
                    board_msg = await reg_channel.send(embed=embed, view=view)
                    set_config("slot_message_id", board_msg.id)
                    print(f"✅ Auto-deployed registration board message: {board_msg.id}", flush=True)
                except Exception as e:
                    print(f"❌ Failed to deploy registration board message: {e}", flush=True)

    async def _auto_provision(self, guild, event_id, count, capacity):
        """Automatically create groups, channels, roles using schedule.json."""
        # Ensure setup channels exist and are configured
        await self.ensure_setup_channels(guild, event_id)
        schedule = load_schedule()
        day_display = get_today_display()
        category_name = f"📋 SCRIMS — {day_display}"

        # Create category
        category = await create_day_category(guild, category_name)
        if not category:
            print("❌ Failed to create day category.", flush=True)
            return

        set_config(f"category_{event_id}", category.id)

        created_groups = []
        for i in range(1, count + 1):
            group_id = generate_group_id(i, event_id)

            # Get schedule for this group number
            sched = None
            for s in schedule:
                if s.get("group_number") == i:
                    sched = s
                    break

            if sched:
                match1 = sched.get("match1", {"idp": "TBD", "start": "TBD", "map": "TBD"})
                match2 = sched.get("match2", {"idp": "TBD", "start": "TBD", "map": "TBD"})
                shift = sched.get("shift", "")
            else:
                match1 = {"idp": "TBD", "start": "TBD", "map": "TBD"}
                match2 = {"idp": "TBD", "start": "TBD", "map": "TBD"}
                shift = ""

            # Create role
            role = await get_or_create_role(guild, group_id, discord.Color.blue())
            if not role:
                continue

            # Create channel
            channel_name = f"group-{i}"
            channel = await create_group_channel(guild, category, channel_name, role)
            if not channel:
                continue

            # Insert group doc
            group_doc = group_model.create_group(
                event_id=event_id,
                group_id=group_id,
                capacity=capacity,
                match1=match1,
                match2=match2,
                channel_id=channel.id,
                role_id=role.id,
                category_id=category.id
            )
            created_groups.append(group_doc)

            # Post initial roster embed in the group channel
            from models import registration as reg_model
            regs = reg_model.get_group_registrations(group_id, event_id)
            roster_embed = build_roster_embed(group_doc, regs, capacity)
            msg = await channel.send(embed=roster_embed)
            group_model.update_roster_message(event_id, group_id, msg.id)

            # Deploy Group Control Panel
            from cogs.admin_panel import GroupControlPanelView
            panel_embed = build_group_control_panel_embed(group_doc)
            await channel.send(embed=panel_embed, view=GroupControlPanelView(event_id, group_id))

            # Rate limit safety
            await asyncio.sleep(0.5)

        # Update the registration board with new groups
        reg_channel_id = get_channel_config("register")
        if reg_channel_id:
            reg_channel = guild.get_channel(reg_channel_id)
            if reg_channel:
                slot_msg_id = get_config("slot_message_id")
                if slot_msg_id:
                    try:
                        all_groups = group_model.get_all_groups(event_id)
                        embed = build_registration_board_embed(all_groups)
                        msg = await reg_channel.fetch_message(slot_msg_id)
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        pass

        return created_groups

    # ═══════════════════ MANUAL PROVISION COMMAND ═══════════════════

    @app_commands.command(
        name="provision",
        description="[Admin] Create today's groups, channels, and roles"
    )
    @app_commands.describe(
        group_count="Number of groups to create (default: from config or 12)",
        capacity="Max teams per group (default: from config or 21)",
        event_name="Display name for the event (default: Scrims Qualifiers)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def provision(
        self,
        interaction: discord.Interaction,
        group_count: int = None,
        capacity: int = None,
        event_name: str = "Scrims Qualifiers"
    ):
        event_id = get_today_event_id()

        # Check if already provisioned
        existing = group_model.get_all_groups(event_id)
        if existing:
            await interaction.response.send_message(
                embed=make_embed(
                    "⚠️ Already Provisioned",
                    f"{Theme.SEP}\n\n"
                    f"Today's groups are already set up!\n"
                    f"**{len(existing)}** groups exist for `{event_id}`.\n\n"
                    f"Use `/deprovision` to tear them down first, or `/addgroups` to add more.\n\n{Theme.SEP}",
                    Theme.WARNING
                ),
                ephemeral=True
            )
            return

        count = group_count or int(get_config("default_group_count", DEFAULT_GROUP_COUNT))
        cap = capacity or int(get_config("default_group_capacity", DEFAULT_GROUP_CAPACITY))

        await interaction.response.defer()

        created = await self._auto_provision(interaction.guild, event_id, count, cap)

        embed = make_embed(
            "✅ Provisioning Complete!",
            f"{Theme.SEP}\n\n"
            f"╭── 📋 **Setup Summary** ──╮\n"
            f"│\n"
            f"│  📅 **Event:** `{event_id}`\n"
            f"│  👥 **Groups:** `{len(created)}`\n"
            f"│  🏟️ **Capacity:** `{cap}` per group\n"
            f"│  🎮 **Total Slots:** `{cap * len(created)}`\n"
            f"│  📋 **Schedule:** Using `schedule.json`\n"
            f"│\n"
            f"╰────────────────────────────╯\n\n"
            f"Created: {len(created)} channels, {len(created)} roles\n\n{Theme.SEP}",
            Theme.SUCCESS,
            f"Provisioned by {interaction.user.display_name}"
        )
        await interaction.followup.send(embed=embed)

    # ─────────────── ADD MORE GROUPS ───────────────

    @app_commands.command(
        name="addgroups",
        description="[Admin] Add more groups to today's event"
    )
    @app_commands.describe(
        count="Number of additional groups to create",
        capacity="Max teams per group (uses today's config if omitted)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def add_groups(self, interaction: discord.Interaction, count: int = 1, capacity: int = None):
        event_id = get_today_event_id()
        existing = group_model.get_all_groups(event_id)

        if not existing:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Provisioned", "Run `/provision` first to set up today's groups."),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        guild = interaction.guild
        cap = capacity or existing[0].get("capacity", DEFAULT_GROUP_CAPACITY)
        category_id = existing[0].get("category_id")
        category = guild.get_channel(category_id) if category_id else None

        if not category:
            await interaction.followup.send(
                embed=error_embed("❌ Category Not Found", "The day's category channel was deleted.")
            )
            return

        start_index = len(existing) + 1
        created = []
        schedule = load_schedule()

        for i in range(start_index, start_index + count):
            group_id = generate_group_id(i, event_id)
            role = await get_or_create_role(guild, group_id, discord.Color.blue())
            if not role:
                continue

            channel = await create_group_channel(guild, category, f"group-{i}", role)
            if not channel:
                continue

            # Try to get schedule for this group number
            sched = None
            for s in schedule:
                if s.get("group_number") == i:
                    sched = s
                    break

            if sched:
                match1 = sched.get("match1", {"idp": "TBD", "start": "TBD", "map": "TBD"})
                match2 = sched.get("match2", {"idp": "TBD", "start": "TBD", "map": "TBD"})
            else:
                match1 = {"idp": "TBD", "start": "TBD", "map": "TBD"}
                match2 = {"idp": "TBD", "start": "TBD", "map": "TBD"}

            group_doc = group_model.create_group(
                event_id, group_id, cap, match1, match2,
                channel.id, role.id, category.id
            )
            created.append(group_doc)

            from models import registration as reg_model
            from utils.embeds import build_roster_embed
            regs = reg_model.get_group_registrations(group_id, event_id)
            msg = await channel.send(embed=build_roster_embed(group_doc, regs, cap))
            group_model.update_roster_message(event_id, group_id, msg.id)

            # Deploy control panel
            from cogs.admin_panel import GroupControlPanelView
            panel_embed = build_group_control_panel_embed(group_doc)
            await channel.send(embed=panel_embed, view=GroupControlPanelView(event_id, group_id))

            await asyncio.sleep(0.5)

        # Refresh availability embed
        await self._refresh_availability(guild, event_id)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Groups Added",
                f"Created **{len(created)}** additional groups.\nTotal: **{len(existing) + len(created)}** groups."
            )
        )

    # ─────────────── DEPROVISION ───────────────

    @app_commands.command(
        name="deprovision",
        description="[Admin] Remove today's groups, channels, and roles"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def deprovision(self, interaction: discord.Interaction):
        event_id = get_today_event_id()
        existing = group_model.get_all_groups(event_id, include_archived=True)

        if not existing:
            await interaction.response.send_message(
                embed=error_embed("❌ Nothing to Remove", "No groups exist for today."),
                ephemeral=True
            )
            return

        await interaction.response.defer()
        await self._cleanup_event(interaction.guild, event_id, existing)

        await interaction.followup.send(
            embed=success_embed(
                "✅ Deprovisioned",
                f"Cleaned up **{len(existing)}** groups for `{event_id}`.\n"
                f"Channels and roles deleted. Data archived."
            )
        )

    # ─────────────── /set_groups COMMAND ───────────────

    @app_commands.command(
        name="set_groups",
        description="[Admin] Set how many groups the bot generates tonight"
    )
    @app_commands.describe(amount="Number of groups (e.g. 10)")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_groups(self, interaction: discord.Interaction, amount: int):
        if amount < 1 or amount > 50:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Amount must be between 1 and 50."),
                ephemeral=True
            )
            return

        set_config("default_group_count", amount)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Group Count Updated",
                f"{Theme.SEP}\n\n"
                f"Tonight's midnight reset will create **{amount}** groups.\n\n{Theme.SEP}"
            ),
            ephemeral=True
        )

    # ─────────────── /update_time COMMAND ───────────────

    @app_commands.command(
        name="update_time",
        description="[Admin] Permanently change a group's default time/map in schedule.json"
    )
    @app_commands.describe(
        group_number="Group number (1-12)",
        match_number="Match number (1 or 2)",
        idp_time="New IDP time (e.g. '01:00 PM')",
        start_time="New start time (e.g. '01:06 PM')",
        map_name="New map name (e.g. ERANGEL)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def update_time(
        self, interaction: discord.Interaction,
        group_number: int, match_number: int,
        idp_time: str = None, start_time: str = None, map_name: str = None
    ):
        if group_number < 1 or group_number > 50:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Group number must be between 1 and 50."),
                ephemeral=True
            )
            return

        if match_number not in (1, 2):
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Match number must be 1 or 2."),
                ephemeral=True
            )
            return

        if not any([idp_time, start_time, map_name]):
            await interaction.response.send_message(
                embed=error_embed("❌ Nothing to Update", "Provide at least one of: idp_time, start_time, map_name."),
                ephemeral=True
            )
            return

        schedule = load_schedule()

        # Find or create the entry
        target = None
        for s in schedule:
            if s.get("group_number") == group_number:
                target = s
                break

        if not target:
            target = {
                "group_number": group_number,
                "shift": "day" if group_number <= 6 else "evening",
                "match1": {"idp": "TBD", "start": "TBD", "map": "TBD"},
                "match2": {"idp": "TBD", "start": "TBD", "map": "TBD"},
            }
            schedule.append(target)

        match_key = f"match{match_number}"
        if idp_time:
            target[match_key]["idp"] = idp_time.strip()
        if start_time:
            target[match_key]["start"] = start_time.strip()
        if map_name:
            target[match_key]["map"] = map_name.strip().upper()

        success = save_schedule(schedule)

        if success:
            updates = []
            if idp_time: updates.append(f"  ◆ **IDP:** `{idp_time}`")
            if start_time: updates.append(f"  ◆ **Start:** `{start_time}`")
            if map_name: updates.append(f"  ◆ **Map:** `{map_name.upper()}`")

            await interaction.response.send_message(
                embed=success_embed(
                    f"✅ Schedule Updated — Group {group_number} Match {match_number}",
                    f"{Theme.SEP}\n\n" + "\n".join(updates) +
                    f"\n\n*Changes saved to `schedule.json` and will take effect tomorrow.*\n\n{Theme.SEP}"
                ),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Save Failed", "Could not write to schedule.json."),
                ephemeral=True
            )

    # ─────────────── INTERNAL CLEANUP ───────────────

    async def _cleanup_event(self, guild, event_id, group_docs):
        """Delete channels, roles, and archive group docs for an event."""
        category_ids = set()

        for g in group_docs:
            ch_id = g.get("channel_id")
            if ch_id:
                await cleanup_channel(guild, ch_id)

            role_id = g.get("role_id")
            if role_id:
                await cleanup_role(guild, role_id)

            cat_id = g.get("category_id")
            if cat_id:
                category_ids.add(cat_id)

            await asyncio.sleep(0.3)

        for cat_id in category_ids:
            await cleanup_category(guild, cat_id)

        group_model.archive_groups(event_id)

    async def _refresh_availability(self, guild, event_id):
        """Refresh the slot availability embed."""
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
                return
            except discord.NotFound:
                pass

        avail_msg_id = get_config(f"slot_availability_msg_{event_id}")
        if avail_msg_id:
            try:
                msg = await channel.fetch_message(avail_msg_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass


async def setup(bot):
    await bot.add_cog(ProvisioningCog(bot))
