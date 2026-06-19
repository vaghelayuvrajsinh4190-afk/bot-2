"""
Mack Bot Tortuga — Provisioning Cog
Handles daily batch group creation (Phase 3a) and nightly cleanup (Phase 3b).
"""

import datetime
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import (
    Theme, TIMEZONE_OFFSET,
    DEFAULT_GROUP_CAPACITY, DEFAULT_GROUP_COUNT
)
from utils.embeds import make_embed, error_embed, success_embed, build_slot_availability_embed
from utils.permissions import (
    get_or_create_role, create_group_channel,
    create_day_category, cleanup_channel, cleanup_role, cleanup_category
)
from models import group as group_model, punishment
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
    """Handles daily group provisioning and nightly cleanup."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Start background tasks."""
        self.midnight_cleanup.start()

    async def cog_unload(self):
        self.midnight_cleanup.cancel()

    # ─────────────── PROVISION COMMAND ───────────────

    @app_commands.command(
        name="provision",
        description="[Admin] Create today's groups, channels, and roles"
    )
    @app_commands.describe(
        group_count="Number of groups to create (default: from config or 10)",
        capacity="Max teams per group (default: from config or 21)",
        event_name="Display name for the event (default: Scrims Qualifiers)",
        match1_start="Match 1 start time (e.g. 2:00 PM)",
        match2_start="Match 2 start time (e.g. 2:30 PM)",
        match1_map="Map for match 1 (default: TBD)",
        match2_map="Map for match 2 (default: TBD)",
        stagger_minutes="Minutes between each group's start time (default: 30)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def provision(
        self,
        interaction: discord.Interaction,
        group_count: int = None,
        capacity: int = None,
        event_name: str = "Scrims Qualifiers",
        match1_start: str = "2:00 PM",
        match2_start: str = "2:30 PM",
        match1_map: str = "TBD",
        match2_map: str = "TBD",
        stagger_minutes: int = 30
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

        # Use config or defaults
        count = group_count or int(get_config("default_group_count", DEFAULT_GROUP_COUNT))
        cap = capacity or int(get_config("default_group_capacity", DEFAULT_GROUP_CAPACITY))

        await interaction.response.defer()

        guild = interaction.guild
        day_display = get_today_display()
        category_name = f"📋 {event_name.upper()} — {day_display}"

        # Create category
        category = await create_day_category(guild, category_name)
        if not category:
            await interaction.followup.send(
                embed=error_embed("❌ Failed", "Could not create the day's category. Check bot permissions.")
            )
            return

        # Store category ID
        set_config(f"category_{event_id}", category.id)

        created_groups = []
        for i in range(1, count + 1):
            group_id = generate_group_id(i, event_id)

            # Create role
            role = await get_or_create_role(guild, group_id, discord.Color.blue())
            if not role:
                continue

            # Create channel
            channel_name = f"group-{group_id.lower()}"
            channel = await create_group_channel(guild, category, channel_name, role)
            if not channel:
                continue

            # Compute match times (stagger by index)
            # For now, store as formatted strings; admin can edit via /panel
            m1_time = f"{match1_start} (+{(i-1)*stagger_minutes}m)" if i > 1 else match1_start
            m2_time = f"{match2_start} (+{(i-1)*stagger_minutes}m)" if i > 1 else match2_start

            match1 = {"idp": m1_time, "start": m1_time, "map": match1_map}
            match2 = {"idp": m2_time, "start": m2_time, "map": match2_map}

            # Insert group doc
            group_doc = group_model.create_group(
                event_id=event_id,
                group_id=group_id,
                capacity=cap,
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
            from utils.embeds import build_roster_embed
            roster_embed = build_roster_embed(group_doc, regs, cap)
            msg = await channel.send(embed=roster_embed)
            group_model.update_roster_message(event_id, group_id, msg.id)

            # Rate limit safety
            await asyncio.sleep(0.5)

        # Post slot availability embed in register channel
        reg_channel_id = get_channel_config("register")
        if reg_channel_id:
            reg_channel = guild.get_channel(reg_channel_id)
            if reg_channel:
                all_groups = group_model.get_all_groups(event_id)
                avail_embed = build_slot_availability_embed(all_groups, event_name)
                
                # Also post the register button
                from cogs.registration import PersistentRegisterView
                avail_msg = await reg_channel.send(embed=avail_embed, view=PersistentRegisterView())
                set_config(f"slot_availability_msg_{event_id}", avail_msg.id)

        # Success response
        embed = make_embed(
            "✅ Provisioning Complete!",
            f"{Theme.SEP}\n\n"
            f"╭── 📋 **Setup Summary** ──╮\n"
            f"│\n"
            f"│  📅 **Event:** `{event_id}`\n"
            f"│  📁 **Category:** `{category_name}`\n"
            f"│  👥 **Groups:** `{len(created_groups)}`\n"
            f"│  🏟️ **Capacity:** `{cap}` per group\n"
            f"│  🎮 **Total Slots:** `{cap * len(created_groups)}`\n"
            f"│\n"
            f"╰────────────────────────────╯\n\n"
            f"Created: {len(created_groups)} channels, {len(created_groups)} roles\n\n{Theme.SEP}",
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

        for i in range(start_index, start_index + count):
            group_id = generate_group_id(i, event_id)
            role = await get_or_create_role(guild, group_id, discord.Color.blue())
            if not role:
                continue

            channel = await create_group_channel(guild, category, f"group-{group_id.lower()}", role)
            if not channel:
                continue

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

    # ─────────────── NIGHTLY CLEANUP TASK ───────────────

    @tasks.loop(minutes=1)
    async def midnight_cleanup(self):
        utc_now = datetime.datetime.utcnow()
        local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)

        if local_now.hour == 0 and local_now.minute == 0:
            print("🕛 MIDNIGHT CLEANUP: Starting nightly cleanup...", flush=True)
            if not self.bot.guilds:
                return

            guild = self.bot.guilds[0]

            # Get yesterday's event ID
            yesterday = (local_now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_groups = group_model.get_all_groups(yesterday, include_archived=True)

            if yesterday_groups:
                await self._cleanup_event(guild, yesterday, yesterday_groups)
                print(f"🧹 Cleaned up {len(yesterday_groups)} groups from {yesterday}", flush=True)

            # Clean up expired bans
            expired_count = punishment.cleanup_expired_bans()
            if expired_count:
                print(f"🧹 Expired {expired_count} bans", flush=True)

            # Log
            log_channel_id = get_channel_config("admin_log")
            if log_channel_id:
                log_ch = guild.get_channel(log_channel_id)
                if log_ch:
                    await log_ch.send(
                        embed=make_embed(
                            "🕛 Nightly Cleanup Complete",
                            f"{Theme.SEP}\n\n"
                            f"**Groups cleaned:** `{len(yesterday_groups)}`\n"
                            f"**Bans expired:** `{expired_count}`\n\n{Theme.SEP}",
                            Theme.SUCCESS
                        )
                    )

            print("✅ Nightly cleanup complete.", flush=True)

    @midnight_cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # ─────────────── INTERNAL CLEANUP ───────────────

    async def _cleanup_event(self, guild, event_id, group_docs):
        """Delete channels, roles, and archive group docs for an event."""
        category_ids = set()

        for g in group_docs:
            # Delete channel
            ch_id = g.get("channel_id")
            if ch_id:
                await cleanup_channel(guild, ch_id)

            # Delete role
            role_id = g.get("role_id")
            if role_id:
                await cleanup_role(guild, role_id)

            cat_id = g.get("category_id")
            if cat_id:
                category_ids.add(cat_id)

            await asyncio.sleep(0.3)

        # Delete categories (after all channels in them are gone)
        for cat_id in category_ids:
            await cleanup_category(guild, cat_id)

        # Archive in database
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
        embed = build_slot_availability_embed(all_groups)

        avail_msg_id = get_config(f"slot_availability_msg_{event_id}")
        if avail_msg_id:
            try:
                msg = await channel.fetch_message(avail_msg_id)
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                pass

        from cogs.registration import PersistentRegisterView
        msg = await channel.send(embed=embed, view=PersistentRegisterView())
        set_config(f"slot_availability_msg_{event_id}", msg.id)


async def setup(bot):
    await bot.add_cog(ProvisioningCog(bot))
