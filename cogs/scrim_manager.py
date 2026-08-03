"""
Mack Bot — Scrim Manager Cog
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Administrative controls for creating and managing independent scrim tiers.

Slash Commands (/scrim):
    create / duplicate  —  Provision new tournament tiers
    settings / modules  —  Configure tier-specific behaviour
    channels / list     —  Manage routing and inspect active tiers
    archive / delete    —  Safely decommission old tiers
"""

import discord
from discord.ext import commands
from discord import app_commands, ui

from config import Theme
from utils.embeds import make_embed, error_embed, success_embed
from models import scrim as scrim_model


# ═══════════════════ AUTOCOMPLETE ═══════════════════

async def scrim_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for scrim_id parameter — shows all scrims."""
    all_scrims = scrim_model.get_all_scrims()
    return [
        app_commands.Choice(name=f"{s['scrim_id']} — {s['name']} ({s['status']})", value=s['scrim_id'])
        for s in all_scrims
        if current.upper() in s.get("scrim_id", "").upper() or current.lower() in s.get("name", "").lower()
    ][:25]


async def active_scrim_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for scrim_id — shows only active scrims."""
    active = scrim_model.get_active_scrims()
    return [
        app_commands.Choice(name=f"{s['scrim_id']} — {s['name']}", value=s['scrim_id'])
        for s in active
        if current.upper() in s.get("scrim_id", "").upper() or current.lower() in s.get("name", "").lower()
    ][:25]


# ═══════════════════ MODALS ═══════════════════

class CreateScrimModal(ui.Modal, title="🏟️ Create New Scrim"):
    """Modal for creating a new scrim."""

    scrim_id_input = ui.TextInput(
        label="Scrim ID (unique, e.g. T3, T2, T1)",
        placeholder="T3",
        max_length=10,
        style=discord.TextStyle.short
    )
    name_input = ui.TextInput(
        label="Display Name",
        placeholder="Tier 3 Scrims",
        max_length=50,
        style=discord.TextStyle.short
    )
    description_input = ui.TextInput(
        label="Description",
        placeholder="Tier 3 competitive scrims",
        max_length=200,
        style=discord.TextStyle.short,
        required=False
    )
    color_input = ui.TextInput(
        label="Embed Color (hex)",
        placeholder="#FF5733",
        max_length=7,
        style=discord.TextStyle.short,
        required=False,
        default="#BF5AF2"
    )
    capacity_input = ui.TextInput(
        label="Teams per group (default 21)",
        placeholder="21",
        max_length=3,
        style=discord.TextStyle.short,
        required=False,
        default="21"
    )

    async def on_submit(self, interaction: discord.Interaction):
        sid = self.scrim_id_input.value.strip().upper()
        name = self.name_input.value.strip()
        desc = self.description_input.value.strip() if self.description_input.value else ""
        color = self.color_input.value.strip() if self.color_input.value else "#BF5AF2"

        if not sid or not name:
            await interaction.response.send_message(
                embed=error_embed("❌ Invalid", "Scrim ID and Name are required."),
                ephemeral=True
            )
            return

        # Check for duplicate
        existing = scrim_model.get_scrim(sid)
        if existing:
            await interaction.response.send_message(
                embed=error_embed("❌ Duplicate", f"Scrim `{sid}` already exists. Use a different ID."),
                ephemeral=True
            )
            return

        # Parse capacity
        try:
            capacity = int(self.capacity_input.value.strip()) if self.capacity_input.value else 21
        except ValueError:
            capacity = 21

        # Validate color
        if not color.startswith("#") or len(color) != 7:
            color = "#BF5AF2"

        settings = {"capacity": capacity}
        scrim_model.create_scrim(
            scrim_id=sid,
            name=name,
            owner_id=str(interaction.user.id),
            description=desc,
            embed_color=color,
            settings=settings
        )

        embed = make_embed(
            "✅ Scrim Created!",
            f"{Theme.SEP}\n\n"
            f"🆔 **Scrim ID:** `{sid}`\n"
            f"📛 **Name:** {name}\n"
            f"📝 **Description:** {desc or 'None'}\n"
            f"🎨 **Color:** `{color}`\n"
            f"👥 **Capacity:** `{capacity}` teams/group\n"
            f"📊 **Status:** 🟢 Active\n\n"
            f"**Next Steps:**\n"
            f"  `/scrim schedule {sid}` — Set up the match schedule\n"
            f"  `/scrim channels {sid}` — Configure channels\n"
            f"  `/scrim modules {sid}` — Enable/disable features\n\n"
            f"{Theme.SEP}",
            Theme.SUCCESS,
            f"Created by {interaction.user.display_name}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EditScrimModal(ui.Modal, title="✏️ Edit Scrim"):
    """Modal for editing scrim details."""

    name_input = ui.TextInput(
        label="Display Name",
        max_length=50,
        style=discord.TextStyle.short
    )
    description_input = ui.TextInput(
        label="Description",
        max_length=200,
        style=discord.TextStyle.short,
        required=False
    )
    color_input = ui.TextInput(
        label="Embed Color (hex)",
        max_length=7,
        style=discord.TextStyle.short,
        required=False
    )
    capacity_input = ui.TextInput(
        label="Teams per group",
        max_length=3,
        style=discord.TextStyle.short,
        required=False
    )

    def __init__(self, scrim_id: str, scrim_doc: dict):
        super().__init__()
        self.scrim_id = scrim_id
        self.name_input.default = scrim_doc.get("name", "")
        self.description_input.default = scrim_doc.get("description", "")
        self.color_input.default = scrim_doc.get("embed_color", "#BF5AF2")
        self.capacity_input.default = str(scrim_doc.get("settings", {}).get("capacity", 21))

    async def on_submit(self, interaction: discord.Interaction):
        updates = {}
        if self.name_input.value.strip():
            updates["name"] = self.name_input.value.strip()
        if self.description_input.value is not None:
            updates["description"] = self.description_input.value.strip()
        if self.color_input.value and self.color_input.value.strip().startswith("#"):
            updates["embed_color"] = self.color_input.value.strip()
        if self.capacity_input.value:
            try:
                updates["settings.capacity"] = int(self.capacity_input.value.strip())
            except ValueError:
                pass

        if updates:
            scrim_model.update_scrim(self.scrim_id, updates)

        await interaction.response.send_message(
            embed=success_embed("✅ Scrim Updated", f"Updated `{self.scrim_id}` successfully."),
            ephemeral=True
        )


# ═══════════════════ COG ═══════════════════

class ScrimManagerCog(commands.Cog):
    """Admin commands for managing scrims."""

    def __init__(self, bot):
        self.bot = bot

    scrim_group = app_commands.Group(
        name="scrim",
        description="Manage scrims — create, edit, delete, configure",
        default_permissions=discord.Permissions(administrator=True)
    )

    # ─────────────── /scrim create ───────────────

    @scrim_group.command(name="create", description="[Admin] Create a new scrim")
    async def scrim_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateScrimModal())

    # ─────────────── /scrim edit ───────────────

    @scrim_group.command(name="edit", description="[Admin] Edit an existing scrim")
    @app_commands.describe(scrim_id="Scrim to edit")
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def scrim_edit(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return
        await interaction.response.send_modal(EditScrimModal(scrim_id, scrim))

    # ─────────────── /scrim delete ───────────────

    @scrim_group.command(name="delete", description="[Admin] Permanently delete a scrim")
    @app_commands.describe(scrim_id="Scrim to delete", confirm="Type the scrim ID to confirm")
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def scrim_delete(self, interaction: discord.Interaction, scrim_id: str, confirm: str):
        if confirm.upper() != scrim_id.upper():
            await interaction.response.send_message(
                embed=error_embed("❌ Confirmation Failed", f"Type `{scrim_id.upper()}` to confirm deletion."),
                ephemeral=True
            )
            return

        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        if scrim_id.upper() == "SQ":
            await interaction.response.send_message(
                embed=error_embed("❌ Protected", "The SQ scrim cannot be deleted. Use `/scrim disable SQ` instead."),
                ephemeral=True
            )
            return

        scrim_model.delete_scrim(scrim_id)
        await interaction.response.send_message(
            embed=success_embed("🗑️ Scrim Deleted", f"Scrim `{scrim_id.upper()}` has been permanently deleted."),
            ephemeral=True
        )

    # ─────────────── /scrim duplicate ───────────────

    @scrim_group.command(name="duplicate", description="[Admin] Clone a scrim with a new ID")
    @app_commands.describe(
        source="Scrim to clone",
        new_id="New unique scrim ID",
        new_name="Display name for the new scrim"
    )
    @app_commands.autocomplete(source=scrim_autocomplete)
    async def scrim_duplicate(self, interaction: discord.Interaction, source: str, new_id: str, new_name: str):
        new_id = new_id.strip().upper()

        existing = scrim_model.get_scrim(new_id)
        if existing:
            await interaction.response.send_message(
                embed=error_embed("❌ Duplicate ID", f"Scrim `{new_id}` already exists."),
                ephemeral=True
            )
            return

        result = scrim_model.duplicate_scrim(source, new_id, new_name, str(interaction.user.id))
        if not result:
            await interaction.response.send_message(
                embed=error_embed("❌ Source Not Found", f"Scrim `{source}` doesn't exist."),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=success_embed(
                "✅ Scrim Duplicated",
                f"Cloned `{source.upper()}` → `{new_id}`\n"
                f"**Name:** {new_name}\n"
                f"All settings, modules, and schedule copied. Channels reset to shared."
            ),
            ephemeral=True
        )

    # ─────────────── /scrim archive ───────────────

    @scrim_group.command(name="archive", description="[Admin] Archive a scrim (preserve data, stop operations)")
    @app_commands.describe(scrim_id="Scrim to archive")
    @app_commands.autocomplete(scrim_id=active_scrim_autocomplete)
    async def scrim_archive(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.archive_scrim(scrim_id)
        await interaction.response.send_message(
            embed=success_embed("📦 Scrim Archived", f"`{scrim_id.upper()}` has been archived. Data is preserved but operations are stopped."),
            ephemeral=True
        )

    # ─────────────── /scrim enable ───────────────

    @scrim_group.command(name="enable", description="[Admin] Enable a disabled/archived scrim")
    @app_commands.describe(scrim_id="Scrim to enable")
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def scrim_enable(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.enable_scrim(scrim_id)
        await interaction.response.send_message(
            embed=success_embed("🟢 Scrim Enabled", f"`{scrim_id.upper()}` is now active."),
            ephemeral=True
        )

    # ─────────────── /scrim disable ───────────────

    @scrim_group.command(name="disable", description="[Admin] Temporarily disable a scrim")
    @app_commands.describe(scrim_id="Scrim to disable")
    @app_commands.autocomplete(scrim_id=active_scrim_autocomplete)
    async def scrim_disable(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.disable_scrim(scrim_id)
        await interaction.response.send_message(
            embed=success_embed("🔴 Scrim Disabled", f"`{scrim_id.upper()}` is now disabled. Data is preserved."),
            ephemeral=True
        )

    # ─────────────── /scrim list ───────────────

    @scrim_group.command(name="list", description="[Admin] Show all scrims with status")
    async def scrim_list(self, interaction: discord.Interaction):
        all_scrims = scrim_model.get_all_scrims()
        if not all_scrims:
            await interaction.response.send_message(
                embed=make_embed("📋 No Scrims", "No scrims found. Use `/scrim create` to add one.", Theme.WARNING),
                ephemeral=True
            )
            return

        lines = []
        for s in all_scrims:
            status_emoji = {"active": "🟢", "disabled": "🔴", "archived": "📦"}.get(s.get("status"), "❓")
            cap = s.get("settings", {}).get("capacity", 21)
            gc = s.get("settings", {}).get("group_count", 12)
            lines.append(
                f"{status_emoji} **{s['scrim_id']}** — {s['name']}\n"
                f"  📊 `{gc}` groups × `{cap}` slots │ {s.get('status', 'unknown')}"
            )

        embed = make_embed(
            f"🏟️ Scrim Manager — {len(all_scrims)} Scrim(s)",
            f"{Theme.SEP}\n\n" + "\n\n".join(lines) + f"\n\n{Theme.SEP}",
            Theme.PREMIUM,
            "Use /scrim info <id> for details"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────── /scrim info ───────────────

    @scrim_group.command(name="info", description="[Admin] Show detailed scrim information")
    @app_commands.describe(scrim_id="Scrim to view")
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def scrim_info(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        settings = scrim.get("settings", {})
        modules = scrim.get("modules", {})
        channels = scrim.get("channels", {})
        status_emoji = {"active": "🟢", "disabled": "🔴", "archived": "📦"}.get(scrim.get("status"), "❓")

        enabled_modules = [k for k, v in modules.items() if v]
        disabled_modules = [k for k, v in modules.items() if not v]

        channel_lines = []
        for ch_type, ch_id in channels.items():
            if ch_id:
                channel_lines.append(f"  ◆ `{ch_type}`: <#{ch_id}>")
            else:
                channel_lines.append(f"  ◆ `{ch_type}`: *shared/global*")

        embed = make_embed(
            f"🏟️ {scrim['name']} — Details",
            f"{Theme.SEP}\n\n"
            f"🆔 **Scrim ID:** `{scrim['scrim_id']}`\n"
            f"📛 **Name:** {scrim['name']}\n"
            f"📝 **Description:** {scrim.get('description', 'None')}\n"
            f"🎨 **Color:** `{scrim.get('embed_color', '#BF5AF2')}`\n"
            f"📊 **Status:** {status_emoji} {scrim.get('status', 'unknown')}\n"
            f"👤 **Owner:** <@{scrim.get('owner_id', 'system')}>\n"
            f"📅 **Created:** `{scrim.get('created_at', 'N/A')[:10]}`\n\n"
            f"{Theme.THIN_SEP}\n\n"
            f"**⚙️ Settings:**\n"
            f"  ◆ Capacity: `{settings.get('capacity', 21)}`\n"
            f"  ◆ Group Count: `{settings.get('group_count', 12)}`\n"
            f"  ◆ Lock Minutes: `{settings.get('lock_minutes', 20)}`\n"
            f"  ◆ Reminder Lead: `{settings.get('reminder_lead_minutes', 30)}`\n"
            f"  ◆ Reg Open: `{settings.get('registration_open_hour', 10)}:{settings.get('registration_open_minute', 0):02d}`\n"
            f"  ◆ Channel Mode: `{settings.get('channel_mode', 'shared')}`\n\n"
            f"**🔌 Modules (ON):** {', '.join(f'`{m}`' for m in enabled_modules[:8]) or 'None'}\n"
            f"**🔌 Modules (OFF):** {', '.join(f'`{m}`' for m in disabled_modules[:8]) or 'None'}\n\n"
            f"**📢 Channels:**\n" + "\n".join(channel_lines) +
            f"\n\n{Theme.SEP}",
            Theme.PREMIUM
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────── /scrim modules ───────────────

    @scrim_group.command(name="modules", description="[Admin] Toggle modules on/off for a scrim")
    @app_commands.describe(
        scrim_id="Scrim to configure",
        module="Module to toggle",
        enabled="Enable or disable"
    )
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    @app_commands.choices(module=[
        app_commands.Choice(name="📝 registration", value="registration"),
        app_commands.Choice(name="📅 schedule", value="schedule"),
        app_commands.Choice(name="👥 teams", value="teams"),
        app_commands.Choice(name="📋 slot_list", value="slot_list"),
        app_commands.Choice(name="🏅 points", value="points"),
        app_commands.Choice(name="🏆 leaderboard", value="leaderboard"),
        app_commands.Choice(name="📊 results", value="results"),
        app_commands.Choice(name="⏰ reminders", value="reminders"),
        app_commands.Choice(name="✅ verification", value="verification"),
        app_commands.Choice(name="📥 check_in", value="check_in"),
        app_commands.Choice(name="🔄 auto_reset", value="auto_reset"),
        app_commands.Choice(name="📝 logging", value="logging"),
        app_commands.Choice(name="📢 announcements", value="announcements"),
    ])
    async def scrim_modules(self, interaction: discord.Interaction, scrim_id: str, module: str, enabled: bool):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.set_scrim_module(scrim_id, module, enabled)
        status = "🟢 Enabled" if enabled else "🔴 Disabled"
        await interaction.response.send_message(
            embed=success_embed("✅ Module Updated", f"**{module}** is now {status} for `{scrim_id.upper()}`"),
            ephemeral=True
        )

    # ─────────────── /scrim settings ───────────────

    @scrim_group.command(name="settings", description="[Admin] View or edit scrim settings")
    @app_commands.describe(
        scrim_id="Scrim to configure",
        setting="Setting to change",
        value="New value"
    )
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    @app_commands.choices(setting=[
        app_commands.Choice(name="👥 capacity", value="capacity"),
        app_commands.Choice(name="🔢 group_count", value="group_count"),
        app_commands.Choice(name="🔒 reserved_slots", value="reserved_slots"),
        app_commands.Choice(name="📁 category_name", value="category_name"),
        app_commands.Choice(name="🏷️ group_naming_format", value="group_naming_format"),
        app_commands.Choice(name="🔢 starting_number", value="starting_number"),
        app_commands.Choice(name="⏰ registration_open_hour", value="registration_open_hour"),
        app_commands.Choice(name="⏰ registration_open_minute", value="registration_open_minute"),
        app_commands.Choice(name="🔒 lock_minutes", value="lock_minutes"),
        app_commands.Choice(name="⏰ reminder_lead_minutes", value="reminder_lead_minutes"),
        app_commands.Choice(name="📢 channel_mode", value="channel_mode"),
        app_commands.Choice(name="🌐 cross_tier_registration", value="cross_tier_registration"),
        app_commands.Choice(name="🛠️ create_group_channels", value="create_group_channels"),
    ])
    async def scrim_settings(self, interaction: discord.Interaction, scrim_id: str, setting: str = None, value: str = None):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        if not setting:
            # Show all settings
            settings = scrim.get("settings", {})
            lines = [f"  ◆ `{k}`: `{v}`" for k, v in settings.items()]
            await interaction.response.send_message(
                embed=make_embed(
                    f"⚙️ {scrim['name']} — Settings",
                    f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
                    Theme.INFO
                ),
                ephemeral=True
            )
            return

        if not value:
            current = scrim_model.get_scrim_setting(scrim_id, setting)
            await interaction.response.send_message(
                embed=make_embed("📋 Current Value", f"**{setting}:** `{current}`", Theme.INFO),
                ephemeral=True
            )
            return

        # Parse value based on setting type
        numeric_settings = {"capacity", "group_count", "reserved_slots", "starting_number", "registration_open_hour",
                           "registration_open_minute", "lock_minutes", "reminder_lead_minutes"}
        if setting in numeric_settings:
            try:
                value = int(value)
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid", f"`{setting}` must be a number."),
                    ephemeral=True
                )
                return


        if setting in ("cross_tier_registration", "create_group_channels"):
            value = value.lower() in ("true", "1", "yes", "on")

        if setting == "access_role_id":
            try:
                value = int(value)
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed("❌ Invalid", f"`{setting}` must be a Role ID (number)."),
                    ephemeral=True
                )
                return

        scrim_model.set_scrim_setting(scrim_id, setting, value)
        await interaction.response.send_message(
            embed=success_embed("✅ Setting Updated", f"**{setting}** set to `{value}` for `{scrim_id.upper()}`"),
            ephemeral=True
        )

    # ─────────────── /scrim category_registration ───────────────

    @scrim_group.command(name="category_registration", description="[Admin] Set the Registration Category for a scrim")
    @app_commands.describe(
        scrim_id="Scrim to configure",
        category="Discord category channel for registration"
    )
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def set_registration_category(self, interaction: discord.Interaction, scrim_id: str, category: discord.CategoryChannel):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.set_scrim_setting(scrim_id, "registration_category_id", category.id)
        scrim_model.set_scrim_setting(scrim_id, "registration_category_name", category.name)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Registration Category Set",
                f"Registration Category for `{scrim_id.upper()}` set to **{category.name}** (`{category.id}`)\n\n"
                f"Future Group Categories will automatically spawn directly below **{category.name}**."
            ),
            ephemeral=True
        )

    # ─────────────── /scrim category_format ───────────────

    @scrim_group.command(name="category_format", description="[Admin] Set Group Category naming format (e.g. '{scrim_id} Group {number:02d}')")
    @app_commands.describe(
        scrim_id="Scrim to configure",
        format_template="Naming template (placeholders: {scrim_name}, {scrim_id}, {number:02d})"
    )
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def set_group_format(self, interaction: discord.Interaction, scrim_id: str, format_template: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.set_scrim_setting(scrim_id, "group_naming_format", format_template)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Naming Format Updated",
                f"Group naming format for `{scrim_id.upper()}` set to:`{format_template}`"
            ),
            ephemeral=True
        )

    # ─────────────── /scrim category_template ───────────────

    @scrim_group.command(name="category_template", description="[Admin] Set Permission Template Category for a scrim")
    @app_commands.describe(
        scrim_id="Scrim to configure",
        category="Discord category to clone permissions from"
    )
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def set_permission_template(self, interaction: discord.Interaction, scrim_id: str, category: discord.CategoryChannel):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.set_scrim_setting(scrim_id, "permission_template_id", category.id)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Permission Template Set",
                f"Permission template for `{scrim_id.upper()}` set to **{category.name}** (`{category.id}`)."
            ),
            ephemeral=True
        )

    # ─────────────── /scrim channels ───────────────

    @scrim_group.command(name="channels", description="[Admin] Configure channels for a scrim")
    @app_commands.describe(
        scrim_id="Scrim to configure",
        channel_type="Channel type to set",
        channel="Channel to assign"
    )
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    @app_commands.choices(channel_type=[
        app_commands.Choice(name="📥 register", value="register"),
        app_commands.Choice(name="📋 admin_log", value="admin_log"),
        app_commands.Choice(name="🏆 leaderboard", value="leaderboard"),
        app_commands.Choice(name="👥 registered_teams", value="registered_teams"),
        app_commands.Choice(name="📊 results", value="results"),
        app_commands.Choice(name="📢 announcements", value="announcements"),
    ])
    async def scrim_channels(self, interaction: discord.Interaction, scrim_id: str,
                            channel_type: str, channel: discord.TextChannel):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        scrim_model.set_scrim_channel(scrim_id, channel_type, channel.id)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Channel Set",
                f"**{channel_type}** channel for `{scrim_id.upper()}` → {channel.mention}"
            ),
            ephemeral=True
        )

    # ─────────────── /scrim teams ───────────────

    @scrim_group.command(name="teams", description="[Admin] List registered teams for a scrim")
    @app_commands.describe(scrim_id="Scrim to view teams for")
    @app_commands.autocomplete(scrim_id=active_scrim_autocomplete)
    async def scrim_teams(self, interaction: discord.Interaction, scrim_id: str):
        await interaction.response.defer(ephemeral=True)

        from config import get_today_event_id
        from models import registration as reg_model

        event_id = get_today_event_id(scrim_id)
        regs = reg_model.get_all_registrations(event_id)

        if not regs:
            await interaction.followup.send(
                embed=make_embed("📋 No Teams", f"No teams registered for `{scrim_id.upper()}` today.", Theme.WARNING),
                ephemeral=True
            )
            return

        lines = []
        for i, r in enumerate(regs[:20], 1):
            lines.append(f"`{i:02d}` 🟢 **{r['team_name']}** — <@{r['owner_id']}> │ Group `{r['group_id']}`")

        remaining = len(regs) - 20
        if remaining > 0:
            lines.append(f"\n*...and {remaining} more*")

        await interaction.followup.send(
            embed=make_embed(
                f"👥 {scrim_id.upper()} — Registered Teams ({len(regs)})",
                f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
                Theme.PREMIUM
            ),
            ephemeral=True
        )

    # ─────────────── /scrim reset ───────────────

    @scrim_group.command(name="reset", description="[Admin] Manually trigger a reset for a scrim")
    @app_commands.describe(scrim_id="Scrim to reset")
    @app_commands.autocomplete(scrim_id=active_scrim_autocomplete)
    async def scrim_reset(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=make_embed(
                "🔄 Reset Queued",
                f"Manual reset for `{scrim_id.upper()}` has been queued.\n"
                f"The provisioning system will handle the full reset cycle.",
                Theme.INFO
            ),
            ephemeral=True
        )

    # ─────────────── /scrim schedule ───────────────

    @scrim_group.command(name="schedule", description="[Admin] View the schedule for a scrim")
    @app_commands.describe(scrim_id="Scrim to view schedule for")
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def scrim_schedule(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        schedule = scrim.get("schedule", [])
        if not schedule:
            await interaction.response.send_message(
                embed=make_embed(
                    f"📅 {scrim['name']} — Schedule",
                    f"No schedule configured.\n\n"
                    f"Use `/scrim duplicate SQ {scrim_id}` to copy SQ's schedule,\n"
                    f"or set up a new schedule via `/update_time`.",
                    Theme.WARNING
                ),
                ephemeral=True
            )
            return

        lines = []
        for entry in schedule[:12]:
            gn = entry.get("group_number", "?")
            m1 = entry.get("match1", {})
            m2 = entry.get("match2", {})
            shift = entry.get("shift", "")
            shift_emoji = "☀️" if shift == "day" else "🌙" if shift == "evening" else "📍"
            lines.append(
                f"{shift_emoji} **G{gn:04d}** — "
                f"M1: `{m1.get('start', 'TBD')}` ({m1.get('map', 'TBD')}) │ "
                f"M2: `{m2.get('start', 'TBD')}` ({m2.get('map', 'TBD')})"
            )

        await interaction.response.send_message(
            embed=make_embed(
                f"📅 {scrim['name']} — Schedule ({len(schedule)} groups)",
                f"{Theme.SEP}\n\n" + "\n".join(lines) + f"\n\n{Theme.SEP}",
                Theme.INFO
            ),
            ephemeral=True
        )

    # ─────────────── /scrim leaderboard ───────────────

    @scrim_group.command(name="leaderboard", description="[Admin] View leaderboard for a scrim")
    @app_commands.describe(scrim_id="Scrim to view leaderboard for")
    @app_commands.autocomplete(scrim_id=active_scrim_autocomplete)
    async def scrim_leaderboard(self, interaction: discord.Interaction, scrim_id: str):
        await interaction.response.send_message(
            embed=make_embed(
                f"🏆 {scrim_id.upper()} Leaderboard",
                "Use the existing `/leaderboard` command with the scrim context.\n"
                "Per-scrim leaderboard filtering is active.",
                Theme.INFO
            ),
            ephemeral=True
        )

    # ─────────────── /scrim logs ───────────────

    @scrim_group.command(name="logs", description="[Admin] View recent activity logs for a scrim")
    @app_commands.describe(scrim_id="Scrim to view logs for")
    @app_commands.autocomplete(scrim_id=scrim_autocomplete)
    async def scrim_logs(self, interaction: discord.Interaction, scrim_id: str):
        scrim = scrim_model.get_scrim(scrim_id)
        if not scrim:
            await interaction.response.send_message(
                embed=error_embed("❌ Not Found", f"Scrim `{scrim_id}` doesn't exist."),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=make_embed(
                f"📜 {scrim['name']} — Logs",
                f"Created: `{scrim.get('created_at', 'N/A')[:19]}`\n"
                f"Updated: `{scrim.get('updated_at', 'N/A')[:19]}`\n"
                f"Status: `{scrim.get('status', 'unknown')}`\n"
                f"Owner: <@{scrim.get('owner_id', 'system')}>\n\n"
                f"*Full activity logging is tracked in the admin log channel.*",
                Theme.INFO
            ),
            ephemeral=True
        )


# ═══════════════════ SETUP ═══════════════════

async def setup(bot: commands.Bot):
    await bot.add_cog(ScrimManagerCog(bot))
