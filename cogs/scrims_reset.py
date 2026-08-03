"""
Mack Bot — Scrims Reset Cog  (v2.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Automated daily category & channel reset engine for independent scrim tiers.

Architecture:
    scrim_data.json   →  Legacy reset-schedule store (hot-reloaded every tick)
    MongoDB (scrims)  →  Authoritative scrim registry for autocomplete & modules

Background Loop:
    60-second polling loop compares current UTC clock against each scrim's
    configured reset time.  Resets are idempotent (one fire per scrim per day).
    Delegates to ProvisioningCog for full tier resets when available.

Slash Commands:
    /toggle_scrim_reset  —  Enable or disable auto-reset for a scrim tier
    /viewscrims          —  Download the current reset config as a .json file

Note:
    Scrim creation and deletion are handled by `/scrim create` and
    `/scrim delete` in the Scrim Manager cog (scrim_manager.py).
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
            
            # Check if auto-reset is enabled for this scrim
            auto_reset = config.get("auto_reset", True)
            if not auto_reset:
                continue

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

    # ═══════════════════ /toggle_scrim_reset ═══════════════════

    @app_commands.command(
        name="toggle_scrim_reset",
        description="[Admin] Enable or disable daily auto-reset for a specific scrim tier",
    )
    @app_commands.describe(
        name="Name of the scrim",
        enable="True to allow daily auto-resets, False to pause them"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle_scrim_reset(self, interaction: discord.Interaction, name: str, enable: bool):
        """Toggle the auto_reset module for a scrim in the MongoDB database."""
        from models import scrim as scrim_model

        scrim_name = name.strip().upper()
        scrim_doc = scrim_model.get_scrim(scrim_name)

        if not scrim_doc:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Not Found",
                    description=f"No scrim called `{scrim_name}` exists.\nUse `/scrim create` to add one.",
                    color=Theme.ERROR,
                ),
                ephemeral=True,
            )
            return

        scrim_model.set_scrim_module(scrim_name, "auto_reset", enable)

        # Also sync to scrim_data.json for the legacy reset loop
        scrims = load_scrims()
        for s in scrims:
            if s.get("name", "").upper() == scrim_name:
                s["auto_reset"] = enable
                break
        save_scrims(scrims)

        status = "✅ Enabled" if enable else "⏸️ Paused"
        embed = discord.Embed(
            title="🔄 Auto-Reset Toggled",
            description=(
                f"{Theme.SEP}\n\n"
                f"**Scrim:** `{scrim_name}`\n"
                f"**Auto-Reset:** {status}\n\n"
                f"📝 Settings saved.\n\n{Theme.SEP}"
            ),
            color=Theme.SUCCESS if enable else Theme.WARNING,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"🔄 [{scrim_name}] Auto-reset set to {enable} by {interaction.user}", flush=True)

    @toggle_scrim_reset.autocomplete("name")
    async def toggle_scrim_reset_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        from models import scrim as scrim_model

        all_scrims = scrim_model.get_all_scrims()
        return [
            app_commands.Choice(name=f"{s['scrim_id']} — {s['name']}", value=s["scrim_id"])
            for s in all_scrims
            if current.upper() in s.get("scrim_id", "").upper()
            or current.lower() in s.get("name", "").lower()
        ][:25]


    # ═══════════════════ /viewscrims ═══════════════════

    @app_commands.command(
        name="viewscrims",
        description="[Admin] View the current scrim reset configurations as a .json file",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def viewscrims(self, interaction: discord.Interaction):
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
                "auto_reset": cfg.get("auto_reset", True),
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
