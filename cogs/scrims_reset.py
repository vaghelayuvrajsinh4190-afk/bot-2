"""
Mack Bot — Scrims Reset Cog
Automated daily category/channel reset for multiple independent scrim tiers.

Features:
  - scrim_data.json stores all scrim tier configs (hot-reloaded every tick)
  - Clock-based 60s loop triggers resets at specific UTC times
  - Anchor-based category positioning (daily category placed below a reference)
  - /add_scrim, /remove_scrim (with autocomplete), /viewconfig slash commands
  - Each scrim tier is fully independent — one failure won't crash others
"""

import io
import os
import json
import datetime
import traceback
import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import Theme, GUILD_ID


# ═══════════════════ JSON DATABASE ═══════════════════

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scrim_data.json")


def load_scrims() -> list[dict]:
    """
    Load scrim configurations from scrim_data.json.
    Returns an empty list if the file doesn't exist or is malformed.
    Auto-creates the file with empty structure on first call.
    """
    if not os.path.exists(DATA_FILE):
        save_scrims([])
        return []

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("scrims", [])
    except (json.JSONDecodeError, KeyError) as e:
        print(f"⚠️ [ScrimsReset] Failed to parse {DATA_FILE}: {e}", flush=True)
        return []


def save_scrims(scrims: list[dict]) -> bool:
    """
    Save scrim configurations to scrim_data.json.
    Uses atomic write (write to .tmp → os.replace) to prevent corruption.
    """
    data = {
        "version": 1,
        "updated_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "scrims": scrims,
    }

    temp_path = DATA_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_path, DATA_FILE)
        return True
    except Exception as e:
        print(f"❌ [ScrimsReset] Failed to save {DATA_FILE}: {e}", flush=True)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False


# ═══════════════════ COG ═══════════════════

class ScrimsResetCog(commands.Cog):
    """Automated daily category/channel reset for multiple scrim tiers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Tracks which scrims have been reset today (prevents double-fires)
        # Key: scrim name (str), Value: "YYYY-MM-DD" date string
        self.last_reset_dates: dict[str, str] = {}

    async def cog_load(self):
        """Start the background reset loop."""
        self.reset_loop.start()
        scrims = load_scrims()
        print(f"  ✅ [ScrimsReset] Loaded {len(scrims)} scrim tier(s) from scrim_data.json", flush=True)

    async def cog_unload(self):
        """Stop the background reset loop."""
        self.reset_loop.cancel()

    # ═══════════════════ CORE RESET LOGIC ═══════════════════

    async def reset_scrim(self, guild: discord.Guild, config: dict) -> bool:
        """
        Execute the full daily reset cycle for a single scrim tier.

        Steps:
            1. Find the anchor category by name
            2. Delete the old daily category and ALL its channels
            3. Create a new daily category positioned directly below the anchor
            4. Auto-generate the configured channels inside it

        Returns True on success, False on failure (will retry next tick).
        """
        scrim_name = config["name"]
        anchor_name = config["anchor_category"]
        daily_name = config["daily_category"]
        channels = config["channels"]

        print(f"\n{'─' * 55}", flush=True)
        print(f"🔄 [{scrim_name}] Starting daily reset...", flush=True)
        print(f"{'─' * 55}", flush=True)

        # ── Step 1: Locate the anchor category ──
        anchor_category = discord.utils.get(guild.categories, name=anchor_name)
        if not anchor_category:
            print(
                f"   ❌ [{scrim_name}] Anchor category '{anchor_name}' not found. "
                f"Skipping. (Check spelling/capitalization)",
                flush=True,
            )
            return False

        print(
            f"   🔍 [{scrim_name}] Anchor found: '{anchor_category.name}' "
            f"(position {anchor_category.position})",
            flush=True,
        )

        # ── Step 2: Wipe out yesterday's daily category ──
        old_category = discord.utils.get(guild.categories, name=daily_name)
        if old_category:
            ch_count = len(old_category.channels)
            print(
                f"   🧹 [{scrim_name}] Cleaning up: '{old_category.name}' "
                f"({ch_count} channel{'s' if ch_count != 1 else ''})",
                flush=True,
            )

            for channel in old_category.channels:
                try:
                    await channel.delete(reason=f"[{scrim_name}] Daily reset — clearing yesterday")
                    print(f"      🗑️ Deleted #{channel.name}", flush=True)
                except discord.Forbidden:
                    print(f"      ⚠️ No permission to delete #{channel.name}", flush=True)
                except discord.HTTPException as e:
                    print(f"      ⚠️ Failed to delete #{channel.name}: {e}", flush=True)

            try:
                await old_category.delete(reason=f"[{scrim_name}] Daily reset — removing old category")
                print(f"      🗑️ Deleted category: '{old_category.name}'", flush=True)
            except discord.Forbidden:
                print(f"      ⚠️ No permission to delete category '{old_category.name}'", flush=True)
            except discord.HTTPException as e:
                print(f"      ⚠️ Failed to delete category: {e}", flush=True)
        else:
            print(f"   ℹ️  [{scrim_name}] No existing '{daily_name}' — fresh start.", flush=True)

        # ── Step 3: Create new daily category below the anchor ──
        new_position = anchor_category.position + 1

        try:
            new_category = await guild.create_category(
                daily_name,
                position=new_position,
                reason=f"[{scrim_name}] Daily reset — creating today's category",
            )
            print(
                f"   ✅ [{scrim_name}] Created '{new_category.name}' at position {new_position}",
                flush=True,
            )
        except discord.Forbidden:
            print(f"   ❌ [{scrim_name}] No permission to create category '{daily_name}'.", flush=True)
            return False
        except discord.HTTPException as e:
            print(f"   ❌ [{scrim_name}] Failed to create category: {e}", flush=True)
            return False

        # ── Step 4: Auto-generate channels ──
        for channel_name in channels:
            try:
                await guild.create_text_channel(
                    channel_name,
                    category=new_category,
                    reason=f"[{scrim_name}] Daily reset — auto-generated channel",
                )
                print(f"      📝 Created #{channel_name}", flush=True)
            except discord.Forbidden:
                print(f"      ⚠️ No permission to create #{channel_name}", flush=True)
            except discord.HTTPException as e:
                print(f"      ⚠️ Failed to create #{channel_name}: {e}", flush=True)

        print(
            f"   🎉 [{scrim_name}] Reset complete! '{daily_name}' below "
            f"'{anchor_name}' with {len(channels)} channels.",
            flush=True,
        )
        return True

    # ═══════════════════ BACKGROUND LOOP ═══════════════════

    @tasks.loop(seconds=60)
    async def reset_loop(self):
        """
        Runs every 60 seconds. Hot-reloads scrim_data.json, checks UTC
        clock against each scrim's reset time, and fires resets as needed.
        """
        guild = self.bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
        if not guild and self.bot.guilds:
            guild = self.bot.guilds[0]
        if not guild:
            return

        scrim_configs = load_scrims()
        if not scrim_configs:
            return

        now_utc = datetime.datetime.utcnow()
        today_str = now_utc.strftime("%Y-%m-%d")

        for config in scrim_configs:
            scrim_name = config.get("name", "Unknown")
            reset_time = config.get("reset_time_utc", {})
            reset_hour = reset_time.get("hour")
            reset_minute = reset_time.get("minute")

            # Skip malformed entries
            if reset_hour is None or reset_minute is None:
                continue

            # Not time yet
            if now_utc.hour != reset_hour or now_utc.minute != reset_minute:
                continue

            # Already reset today (idempotency guard)
            if self.last_reset_dates.get(scrim_name) == today_str:
                continue

            # Execute with error isolation
            try:
                # Use ProvisioningCog to handle the full tier reset (category + groups + board + cleanup)
                prov_cog = self.bot.get_cog("ProvisioningCog")
                if prov_cog:
                    success = await prov_cog.tier_reset(guild, config)
                else:
                    success = await self.reset_scrim(guild, config) # Fallback to basic reset
                    
                if success:
                    self.last_reset_dates[scrim_name] = today_str
                    print(f"✅ [{scrim_name}] Marked as reset for {today_str}", flush=True)
            except Exception as e:
                print(f"❌ [{scrim_name}] Unhandled error during reset: {e}", flush=True)
                traceback.print_exc()

    @reset_loop.before_loop
    async def before_reset_loop(self):
        await self.bot.wait_until_ready()

    # ═══════════════════ /add_scrim ═══════════════════

    @app_commands.command(
        name="add_scrim",
        description="[Admin] Add a new scrim tier to the daily reset schedule",
    )
    @app_commands.describe(
        name="Unique scrim name (e.g., SQ, T3, T2)",
        anchor_category="Name of the existing category to position below",
        daily_category="Name of the category that gets created/reset daily",
        channels="Comma-separated channel names (e.g., room-id-pass,results)",
        reset_hour="Reset hour in UTC (0–23)",
        reset_minute="Reset minute in UTC (0–59)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def add_scrim(
        self,
        interaction: discord.Interaction,
        name: str,
        anchor_category: str,
        daily_category: str,
        channels: str,
        reset_hour: app_commands.Range[int, 0, 23],
        reset_minute: app_commands.Range[int, 0, 59],
    ):
        """Add a new scrim tier to scrim_data.json."""
        scrim_name = name.strip().upper()

        scrims = load_scrims()

        # Duplicate check
        if scrim_name in [s["name"].upper() for s in scrims]:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Duplicate Name",
                    description=(
                        f"A scrim called `{scrim_name}` already exists.\n"
                        f"Use `/remove_scrim` first if you want to replace it."
                    ),
                    color=Theme.ERROR,
                ),
                ephemeral=True,
            )
            return

        # Parse channel list
        channel_list = [
            ch.strip().lower().replace(" ", "-")
            for ch in channels.split(",")
            if ch.strip()
        ]
        if not channel_list:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ No Channels",
                    description="Provide at least one channel name (comma-separated).",
                    color=Theme.ERROR,
                ),
                ephemeral=True,
            )
            return

        new_scrim = {
            "name": scrim_name,
            "anchor_category": anchor_category.strip(),
            "daily_category": daily_category.strip(),
            "channels": channel_list,
            "reset_time_utc": {"hour": reset_hour, "minute": reset_minute},
        }

        scrims.append(new_scrim)

        if save_scrims(scrims):
            embed = discord.Embed(
                title="✅ Scrim Added",
                description=(
                    f"{Theme.SEP}\n\n"
                    f"**Name:** `{scrim_name}`\n"
                    f"**Anchor:** `{anchor_category.strip()}`\n"
                    f"**Daily Category:** `{daily_category.strip()}`\n"
                    f"**Channels:** `{', '.join(channel_list)}`\n"
                    f"**Reset Time:** `{reset_hour:02d}:{reset_minute:02d} UTC`\n\n"
                    f"📝 Saved — takes effect automatically, no restart needed.\n\n{Theme.SEP}"
                ),
                color=Theme.SUCCESS,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            print(f"📥 [{scrim_name}] Added by {interaction.user}", flush=True)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Save Failed",
                    description="Could not write to `scrim_data.json`. Check file permissions.",
                    color=Theme.ERROR,
                ),
                ephemeral=True,
            )

    # ═══════════════════ /remove_scrim ═══════════════════

    @app_commands.command(
        name="remove_scrim",
        description="[Admin] Remove a scrim tier from the daily reset schedule",
    )
    @app_commands.describe(name="Name of the scrim to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_scrim(self, interaction: discord.Interaction, name: str):
        """Remove a scrim tier by name from scrim_data.json."""
        scrim_name = name.strip().upper()

        scrims = load_scrims()
        original_count = len(scrims)
        scrims = [s for s in scrims if s["name"].upper() != scrim_name]

        if len(scrims) == original_count:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Not Found",
                    description=(
                        f"No scrim called `{scrim_name}` exists.\n"
                        f"Current scrims: {', '.join(s['name'] for s in scrims) or 'None'}"
                    ),
                    color=Theme.ERROR,
                ),
                ephemeral=True,
            )
            return

        if save_scrims(scrims):
            self.last_reset_dates.pop(scrim_name, None)
            embed = discord.Embed(
                title="✅ Scrim Removed",
                description=(
                    f"{Theme.SEP}\n\n"
                    f"Deleted `{scrim_name}` from the schedule.\n"
                    f"Remaining scrims: **{len(scrims)}**\n\n"
                    f"⚠️ Existing Discord categories/channels are **not** auto-deleted.\n\n{Theme.SEP}"
                ),
                color=Theme.SUCCESS,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            print(f"🗑️ [{scrim_name}] Removed by {interaction.user}", flush=True)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Save Failed",
                    description="Could not write to `scrim_data.json`.",
                    color=Theme.ERROR,
                ),
                ephemeral=True,
            )

    @remove_scrim.autocomplete("name")
    async def remove_scrim_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete populated from live scrim_data.json."""
        scrims = load_scrims()
        return [
            app_commands.Choice(name=s["name"], value=s["name"])
            for s in scrims
            if current.upper() in s.get("name", "").upper()
        ][:25]

    # ═══════════════════ /viewconfig ═══════════════════

    @app_commands.command(
        name="viewconfig",
        description="[Admin] View the current scrim reset configurations as a .json file",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def viewconfig(self, interaction: discord.Interaction):
        """
        Download the full scrim config as a .json file attachment.
        Uses file attachment (not embeds) to avoid Discord's 6000-char embed limit.
        """
        scrims = load_scrims()
        today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")

        config_snapshot = {
            "server_id": int(GUILD_ID) if GUILD_ID else None,
            "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "total_scrims": len(scrims),
            "scrims": [],
        }

        for cfg in scrims:
            scrim_name = cfg.get("name", "Unknown")
            rt = cfg.get("reset_time_utc", {})
            h = rt.get("hour", 0)
            m = rt.get("minute", 0)

            config_snapshot["scrims"].append({
                "name": scrim_name,
                "anchor_category": cfg.get("anchor_category", "N/A"),
                "daily_category": cfg.get("daily_category", "N/A"),
                "channels": cfg.get("channels", []),
                "reset_time_utc": f"{h:02d}:{m:02d}",
                "last_reset_date": self.last_reset_dates.get(scrim_name, "Never"),
                "status": (
                    "✅ Reset today"
                    if self.last_reset_dates.get(scrim_name) == today_str
                    else "⏳ Pending"
                ),
            })

        json_str = json.dumps(config_snapshot, indent=4, ensure_ascii=False)
        file = discord.File(
            fp=io.BytesIO(json_str.encode("utf-8")),
            filename="scrim_config.json",
        )

        await interaction.response.send_message(
            content=f"📋 **Scrim Reset Config** ({len(scrims)} tier{'s' if len(scrims) != 1 else ''}):",
            file=file,
            ephemeral=True,
        )


# ═══════════════════ SETUP ═══════════════════

async def setup(bot: commands.Bot):
    await bot.add_cog(ScrimsResetCog(bot))
