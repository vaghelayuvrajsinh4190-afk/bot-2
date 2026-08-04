import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
import json
import traceback

from config import get_today_event_id, save_schedule, Theme
from models import scrim as scrim_model
from models import group as group_model
from utils.embeds import make_embed, success_embed, error_embed

class GroupsModal(discord.ui.Modal, title="Configure Groups"):
    group_count = discord.ui.TextInput(label="Group Count", placeholder="e.g. 12", max_length=3)
    capacity = discord.ui.TextInput(label="Capacity (Teams per group)", placeholder="e.g. 21", max_length=3)
    category_name = discord.ui.TextInput(label="Category Name", placeholder="e.g. 🏆・[SQ] SCRIMS", max_length=100)

    def __init__(self, scrim_id: str, current_settings: dict):
        super().__init__()
        self.scrim_id = scrim_id
        self.group_count.default = str(current_settings.get("group_count", 12))
        self.capacity.default = str(current_settings.get("capacity", 21))
        self.category_name.default = current_settings.get("category_name", f"🏆・[{scrim_id.upper()}] SCRIMS")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            count = int(self.group_count.value)
            cap = int(self.capacity.value)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid Input", "Group count and capacity must be integers."), ephemeral=True)

        await asyncio.to_thread(scrim_model.set_scrim_setting, self.scrim_id, "group_count", count)
        await asyncio.to_thread(scrim_model.set_scrim_setting, self.scrim_id, "capacity", cap)
        await asyncio.to_thread(scrim_model.set_scrim_setting, self.scrim_id, "category_name", self.category_name.value)
        
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed)


def _subtract_6_minutes(time_str: str) -> str:
    """Subtract 6 minutes from a time string like '06:00 PM' to get IDP time."""
    try:
        dt = datetime.datetime.strptime(time_str.strip(), "%I:%M %p")
        dt -= datetime.timedelta(minutes=6)
        return dt.strftime("%I:%M %p")
    except ValueError:
        return time_str

class ScheduleModal(discord.ui.Modal):
    def __init__(self, scrim_id: str, entry: dict = None, bot: commands.Bot = None):
        title = "Edit Group Schedule" if entry else "Add New Group"
        super().__init__(title=title)
        self.scrim_id = scrim_id
        self.entry = entry
        self.bot = bot

        self.group_number = discord.ui.TextInput(
            label="Group Number", 
            placeholder="e.g. 1", 
            max_length=3,
            default=str(entry.get("group_number", "")) if entry else ""
        )
        self.shift = discord.ui.TextInput(
            label="Shift Name", 
            placeholder="e.g. evening", 
            required=False,
            default=entry.get("shift", "") if entry else ""
        )
        m1 = entry.get("match1", {}) if entry else {}
        self.match1 = discord.ui.TextInput(
            label="Match 1 (Time | Map)", 
            placeholder="10:00 PM | Erangel",
            default=f"{m1.get('start', 'TBD')} | {m1.get('map', 'TBD')}" if entry else ""
        )
        m2 = entry.get("match2", {}) if entry else {}
        self.match2 = discord.ui.TextInput(
            label="Match 2 (Time | Map)", 
            placeholder="10:45 PM | Miramar",
            default=f"{m2.get('start', 'TBD')} | {m2.get('map', 'TBD')}" if entry else ""
        )
        self.add_item(self.group_number)
        self.add_item(self.shift)
        self.add_item(self.match1)
        self.add_item(self.match2)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Access Denied", "Admins only."), ephemeral=True)

        try:
            grp_num = int(self.group_number.value)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid Input", "Group number must be an integer."), ephemeral=True)

        def parse_match(val):
            parts = [p.strip() for p in val.split("|")]
            start = parts[0] if len(parts) > 0 and parts[0] else "TBD"
            m_map = parts[1] if len(parts) > 1 and parts[1] else "TBD"
            idp = _subtract_6_minutes(start) if start != "TBD" else "TBD"
            return {"idp": idp, "start": start, "map": m_map}

        m1_data = parse_match(self.match1.value)
        m2_data = parse_match(self.match2.value)

        schedule = await asyncio.to_thread(scrim_model.get_scrim_schedule, self.scrim_id)
        if schedule is None:
            schedule = []

        found = False
        for i, g in enumerate(schedule):
            if g.get("group_number") == grp_num:
                schedule[i] = {
                    "group_number": grp_num,
                    "shift": self.shift.value or f"Group {grp_num}",
                    "match1": m1_data,
                    "match2": m2_data
                }
                found = True
                break
        
        if not found:
            schedule.append({
                "group_number": grp_num,
                "shift": self.shift.value or f"Group {grp_num}",
                "match1": m1_data,
                "match2": m2_data
            })
        
        schedule.sort(key=lambda x: x.get("group_number", 0))

        success = await asyncio.to_thread(save_schedule, schedule, self.scrim_id)
        if success:
            scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
            embed = ScrimConfigCog.build_panel_embed(scrim)
            await interaction.response.edit_message(embed=embed, view=ScrimConfigPanel(self.scrim_id, self.bot))
        else:
            await interaction.response.send_message(embed=error_embed("Error", "Failed to save schedule."), ephemeral=True)

class ConfirmRemoveView(discord.ui.View):
    def __init__(self, scrim_id: str, group_number: int, bot):
        super().__init__(timeout=120)
        self.scrim_id = scrim_id
        self.group_number = group_number
        self.bot = bot

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger)
    async def btn_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admins only.", ephemeral=True)
            
        schedule = await asyncio.to_thread(scrim_model.get_scrim_schedule, self.scrim_id)
        schedule = [g for g in schedule if g.get("group_number") != self.group_number]
        
        await asyncio.to_thread(save_schedule, schedule, self.scrim_id)
        
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed, view=ScrimConfigPanel(self.scrim_id, self.bot))
        
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def btn_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed, view=ScrimConfigPanel(self.scrim_id, self.bot))

class RemoveGroupSelect(discord.ui.Select):
    def __init__(self, scrim_id: str, schedule: list, bot):
        self.scrim_id = scrim_id
        self.schedule = schedule
        self.bot = bot
        options = []
        for g in schedule[:25]:
            grp = g.get("group_number")
            shift = g.get("shift", f"Group {grp}")
            options.append(discord.SelectOption(label=f"Group {grp}", description=f"Shift: {shift}", value=str(grp)))
        if not options:
            options.append(discord.SelectOption(label="No groups to remove", value="none"))
        super().__init__(placeholder="Select a group to remove...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admins only.", ephemeral=True)
            
        val = self.values[0]
        if val == "none":
            return await interaction.response.send_message("No groups available.", ephemeral=True)
            
        grp_num = int(val)
        embed = make_embed("Confirm Removal", f"Are you sure you want to remove Group {grp_num} from the schedule?", Theme.WARNING)
        await interaction.response.edit_message(embed=embed, view=ConfirmRemoveView(self.scrim_id, grp_num, self.bot))

class RemoveGroupView(discord.ui.View):
    def __init__(self, scrim_id: str, schedule: list, bot):
        super().__init__(timeout=120)
        self.add_item(RemoveGroupSelect(scrim_id, schedule, bot))
        self.scrim_id = scrim_id
        self.bot = bot
        
    @discord.ui.button(label="Back to Panel", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed, view=ScrimConfigPanel(self.scrim_id, self.bot))

class ScheduleControlSelect(discord.ui.Select):
    def __init__(self, scrim_id: str, schedule: list, bot):
        self.scrim_id = scrim_id
        self.schedule = schedule
        self.bot = bot
        options = []
        for g in schedule[:23]:
            grp = g.get("group_number")
            shift = g.get("shift", f"Group {grp}")
            options.append(discord.SelectOption(label=f"Edit Group {grp}", description=shift, value=f"edit_{grp}"))
            
        options.append(discord.SelectOption(label="➕ Add New Group", value="add_new"))
        if schedule:
            options.append(discord.SelectOption(label="🗑️ Remove Group", value="remove_group"))
        super().__init__(placeholder="Select an action or group...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admins only.", ephemeral=True)
            
        val = self.values[0]
        if val == "add_new":
            await interaction.response.send_modal(ScheduleModal(self.scrim_id, None, self.bot))
        elif val == "remove_group":
            embed = make_embed("Remove Group", "Select a group to remove from the schedule.", Theme.WARNING)
            await interaction.response.edit_message(embed=embed, view=RemoveGroupView(self.scrim_id, self.schedule, self.bot))
        elif val.startswith("edit_"):
            grp_num = int(val.split("_")[1])
            entry = next((g for g in self.schedule if g.get("group_number") == grp_num), None)
            if entry:
                await interaction.response.send_modal(ScheduleModal(self.scrim_id, entry, self.bot))

class ScheduleControlView(discord.ui.View):
    def __init__(self, scrim_id: str, schedule: list, bot):
        super().__init__(timeout=120)
        self.add_item(ScheduleControlSelect(scrim_id, schedule, bot))
        self.scrim_id = scrim_id
        self.bot = bot
        
    @discord.ui.button(label="Back to Panel", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed, view=ScrimConfigPanel(self.scrim_id, self.bot))

class CustomPointsModal(discord.ui.Modal, title="Custom Points JSON"):
    points_json = discord.ui.TextInput(
        label="Points Config JSON",
        style=discord.TextStyle.paragraph,
        placeholder='{"kill_points": 1, "position_points": {"1": 15, "2": 12}}'
    )

    def __init__(self, scrim_id: str):
        super().__init__()
        self.scrim_id = scrim_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            data = json.loads(self.points_json.value)
            if "kill_points" not in data or "position_points" not in data:
                raise ValueError("Missing kill_points or position_points.")
        except Exception as e:
            return await interaction.response.send_message(embed=error_embed("Invalid JSON", str(e)), ephemeral=True)

        await asyncio.to_thread(scrim_model.update_scrim, self.scrim_id, {"points_config": data})
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed)


class SlotsModal(discord.ui.Modal, title="Configure Reserved Slots"):
    reserved_slots = discord.ui.TextInput(label="Reserved Slots", placeholder="e.g. 2", max_length=2)

    def __init__(self, scrim_id: str, current_slots: int):
        super().__init__()
        self.scrim_id = scrim_id
        self.reserved_slots.default = str(current_slots)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            slots = int(self.reserved_slots.value)
        except ValueError:
            return await interaction.response.send_message(embed=error_embed("Invalid Input", "Must be an integer."), ephemeral=True)

        await asyncio.to_thread(scrim_model.set_scrim_setting, self.scrim_id, "reserved_slots", slots)
        
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed)


class PointsSelect(discord.ui.Select):
    def __init__(self, scrim_id: str):
        self.scrim_id = scrim_id
        options = [
            discord.SelectOption(label="Standard (15pt win)", value="standard", description="Default scoring"),
            discord.SelectOption(label="PMGC (10pt win)", value="pmgc", description="Official PMGC points"),
            discord.SelectOption(label="Kill Focused (10pt win, 2pt kill)", value="kill_focused", description="High kill reward"),
            discord.SelectOption(label="Custom", value="custom", description="Enter raw JSON"),
        ]
        super().__init__(placeholder="Select Points Format...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "custom":
            await interaction.response.send_modal(CustomPointsModal(self.scrim_id))
            return
        
        config = {"kill_points": 1, "position_points": {}}
        if val == "standard":
            config["position_points"] = {"1": 15, "2": 12, "3": 10, "4": 8, "5": 6, "6": 4, "7": 2, "8": 1, "9": 0, "10": 0, "11": 0, "12": 0, "13": 0, "14": 0, "15": 0, "16": 0}
        elif val == "pmgc":
            config["position_points"] = {"1": 10, "2": 6, "3": 5, "4": 4, "5": 3, "6": 2, "7": 1, "8": 1, "9": 0, "10": 0, "11": 0, "12": 0, "13": 0, "14": 0, "15": 0, "16": 0}
        elif val == "kill_focused":
            config["kill_points"] = 2
            config["position_points"] = {"1": 10, "2": 6, "3": 5, "4": 4, "5": 3, "6": 2, "7": 1, "8": 1, "9": 0, "10": 0, "11": 0, "12": 0, "13": 0, "14": 0, "15": 0, "16": 0}

        await asyncio.to_thread(scrim_model.update_scrim, self.scrim_id, {"points_config": config})
        
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed, view=interaction.view)

class ScrimConfigPanel(discord.ui.View):
    def __init__(self, scrim_id: str, bot: commands.Bot):
        super().__init__(timeout=None)
        self.scrim_id = scrim_id
        self.bot = bot
        self.add_item(PointsSelect(scrim_id))

    @discord.ui.button(label="Groups", style=discord.ButtonStyle.primary, row=1, custom_id="panel_groups")
    async def btn_groups(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        if not scrim:
            return await interaction.response.send_message("Scrim not found.", ephemeral=True)
        await interaction.response.send_modal(GroupsModal(self.scrim_id, scrim.get("settings", {})))

    @discord.ui.button(label="Schedule", style=discord.ButtonStyle.primary, row=1, custom_id="panel_schedule")
    async def btn_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        schedule = await asyncio.to_thread(scrim_model.get_scrim_schedule, self.scrim_id)
        embed = make_embed("📅 Schedule Control Panel", f"Manage the schedule for `{self.scrim_id.upper()}`.\n\nUse the dropdown below to Edit a Group, Add a New Group, or Remove a Group.", Theme.PREMIUM)
        await interaction.response.edit_message(embed=embed, view=ScheduleControlView(self.scrim_id, schedule, self.bot))

    @discord.ui.button(label="Slots", style=discord.ButtonStyle.primary, row=1, custom_id="panel_slots")
    async def btn_slots(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        if not scrim:
            return await interaction.response.send_message("Scrim not found.", ephemeral=True)
        slots = scrim.get("settings", {}).get("reserved_slots", 1)
        await interaction.response.send_modal(SlotsModal(self.scrim_id, slots))

    @discord.ui.button(label="Provision / Post to Registration", style=discord.ButtonStyle.success, row=2, custom_id="panel_provision")
    async def btn_provision(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
            if not scrim:
                return await interaction.followup.send("Scrim not found.")
            
            settings = scrim.get("settings", {})
            count = int(settings.get("group_count", 12))
            capacity = int(settings.get("capacity", 21))
            reserved = int(settings.get("reserved_slots", 1))
            category_name = settings.get("category_name", f"🏆・[{self.scrim_id.upper()}] SCRIMS")
            
            event_id = get_today_event_id(self.scrim_id)

            prov_cog = self.bot.get_cog("ProvisioningCog")
            if not prov_cog:
                return await interaction.followup.send("ProvisioningCog not loaded.")

            await prov_cog._auto_provision(
                guild=interaction.guild,
                event_id=event_id,
                count=count,
                capacity=capacity,
                category_name=category_name,
                scrim_id=self.scrim_id
            )

            # Update reserved slots for already created groups
            updated = await asyncio.to_thread(group_model.set_all_reserved_slots, event_id, reserved, self.scrim_id)

            embed = success_embed(
                "Provisioning Complete",
                f"**Tier:** {self.scrim_id.upper()}\n"
                f"**Event ID:** {event_id}\n"
                f"**Groups:** {count}\n"
                f"**Capacity:** {capacity}\n"
                f"**Category:** {category_name}\n"
                f"**Reserved Slots Pushed:** {reserved} (Updated {updated} existing groups)"
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(embed=error_embed("Provisioning Failed", str(e)))

    @discord.ui.button(label="Refresh Panel", style=discord.ButtonStyle.secondary, row=2, custom_id="panel_refresh")
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        scrim = await asyncio.to_thread(scrim_model.get_scrim, self.scrim_id)
        if not scrim:
            return await interaction.response.send_message("Scrim not found.", ephemeral=True)
        
        embed = ScrimConfigCog.build_panel_embed(scrim)
        await interaction.response.edit_message(embed=embed, view=self)


class ScrimConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def build_panel_embed(scrim_doc: dict):
        scrim_id = scrim_doc.get("scrim_id", "SQ")
        settings = scrim_doc.get("settings", {})
        schedule = scrim_doc.get("schedule", [])
        pts = scrim_doc.get("points_config", {})
        
        desc = (
            f"**ID:** `{scrim_id}`\n"
            f"**Name:** `{scrim_doc.get('name', 'N/A')}`\n\n"
            f"**__Settings__**\n"
            f"• **Group Count:** `{settings.get('group_count', 'N/A')}`\n"
            f"• **Capacity:** `{settings.get('capacity', 'N/A')}`\n"
            f"• **Reserved Slots:** `{settings.get('reserved_slots', 'N/A')}`\n"
            f"• **Category:** `{settings.get('category_name', 'N/A')}`\n\n"
            f"**__Schedule ({len(schedule)} groups)__**\n"
        )
        
        for entry in schedule[:5]:
            gn = entry.get("group_number", "?")
            m1 = entry.get("match1", {})
            m2 = entry.get("match2", {})
            shift = entry.get("shift", "").lower()
            shift_emoji = "☀️" if shift == "day" else "🌙" if shift == "evening" else "📍"
            desc += (
                f"{shift_emoji} **G{gn:02d}** — "
                f"`{m1.get('start', 'TBD')}` ({m1.get('idp', 'TBD')}) & "
                f"`{m2.get('start', 'TBD')}` ({m2.get('idp', 'TBD')})\n"
            )
        
        if len(schedule) > 5:
            desc += f"• *... and {len(schedule)-5} more*\n"
        
        desc += f"\n**__Points__**\n• Kill: `{pts.get('kill_points', 'N/A')}`\n"
        desc += f"• 1st Place: `{pts.get('position_points', {}).get('1', 'N/A')}`"

        return make_embed(f"⚙️ {scrim_id.upper()} Control Panel", desc, Theme.PREMIUM)

    @app_commands.command(name="scrim_panel", description="Post the Scrim Configuration Panel")
    @app_commands.default_permissions(administrator=True)
    async def scrim_panel(self, interaction: discord.Interaction, scrim_id: str):
        scrim_id = scrim_id.upper()
        scrim = await asyncio.to_thread(scrim_model.ensure_scrim_exists, scrim_id, str(interaction.user.id))
        
        embed = self.build_panel_embed(scrim)
        view = ScrimConfigPanel(scrim_id, self.bot)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(ScrimConfigCog(bot))
