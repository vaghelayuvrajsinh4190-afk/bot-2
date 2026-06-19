"""
Mack Bot Tortuga — Embed Utilities
Shared embed builder and visual helpers.
"""

import datetime
import discord
from config import Theme


def make_embed(title, desc=None, color=None, footer=None):
    """Create a styled embed with consistent formatting."""
    e = discord.Embed(
        title=title,
        description=desc,
        color=color or Theme.INFO,
        timestamp=datetime.datetime.utcnow()
    )
    e.set_footer(text=footer or Theme.FOOTER)
    return e


def success_embed(title, desc):
    return make_embed(title, desc, Theme.SUCCESS)


def error_embed(title, desc):
    return make_embed(title, desc, Theme.ERROR)


def warning_embed(title, desc):
    return make_embed(title, desc, Theme.WARNING)


def build_roster_embed(group_doc, registrations, capacity):
    """
    Build the live roster embed for a group channel.
    
    Args:
        group_doc: The group document from MongoDB
        registrations: List of registration documents for this group
        capacity: Max teams in the group
    """
    group_id = group_doc.get("group_id", "????")
    count = len(registrations)
    display_name = f"Group {group_id}"
    status = Theme.group_status(count, capacity)
    color = Theme.group_color(count, capacity)
    bar = Theme.bar(count, capacity, 16)

    # Build the match info
    match1 = group_doc.get("match1", {})
    match2 = group_doc.get("match2", {})
    m1_idp = match1.get("idp", "TBD")
    m1_start = match1.get("start", "TBD")
    m1_map = match1.get("map", "TBD")
    m2_idp = match2.get("idp", "TBD")
    m2_start = match2.get("start", "TBD")
    m2_map = match2.get("map", "TBD")

    # Build table lines
    table_lines = []
    for i in range(capacity):
        num = f"{i+1:02d}"
        if i < len(registrations):
            reg = registrations[i]
            tn = reg.get("team_name", "Unknown")
            tn = (tn[:20] + '..') if len(tn) > 20 else tn
            captain = reg.get("owner_id", "")
            table_lines.append(f" {num} │ ✦ {tn:<20} │ <@{captain}>")
        else:
            table_lines.append(f" {num} │ ▱ ── Open Slot ──")

    header = f"  #  │ TEAM NAME\n ────┼───────────────────"
    tabular_data = header + "\n" + "\n".join(table_lines)

    embed = make_embed(
        f"🏆  {display_name}  ─  Live Roster",
        f"📡 **Status:** {status} │ **{count}/{capacity}** Slots Filled\n"
        f"▓ **Roster Fill:** {bar}\n\n"
        f"╭── 🎮 **Match Details** ──╮\n"
        f"│  **Match 1:** `{m1_start}` │ IDP `{m1_idp}`\n"
        f"│  📍 Map: `{m1_map}`\n"
        f"│\n"
        f"│  **Match 2:** `{m2_start}` │ IDP `{m2_idp}`\n"
        f"│  📍 Map: `{m2_map}`\n"
        f"╰──────────────────────────╯",
        color=color,
        footer="🔄 Auto-updates • Do not type here"
    )
    embed.add_field(name="📋 **Registered Squads**", value=f"```\n{tabular_data}\n```", inline=False)
    return embed


def build_slot_availability_embed(groups, event_name="Scrims Qualifiers"):
    """
    Build the slot availability embed showing all groups with progress bars.
    Posted in #register-here and updated on every registration.
    
    Args:
        groups: List of group documents
        event_name: Display name for the event
    """
    lines = []
    total_filled = 0
    total_capacity = 0

    for g in groups:
        gid = g.get("group_id", "????")
        count = g.get("current_count", 0)
        cap = g.get("capacity", 21)
        total_filled += count
        total_capacity += cap

        status = Theme.group_status(count, cap)
        bar = Theme.bar(count, cap, 10)

        m1 = g.get("match1", {})
        m2 = g.get("match2", {})
        m1_start = m1.get("start", "TBD")
        m2_start = m2.get("start", "TBD")

        lines.append(
            f"**✦ Group {gid}** ── {status}\n"
            f"  {bar}  `{count}/{cap} filled`\n"
            f"  ⏱ **Matchtimes:** `{m1_start}` │ `{m2_start}`"
        )

    overall_bar = Theme.bar(total_filled, total_capacity, 18)
    groups_text = "\n\n".join(lines) if lines else "*No groups available yet.*"

    embed = make_embed(
        f"📋 {event_name} ─ Slot Availability",
        f"📊 **Overall Stats:** `{total_filled}/{total_capacity}` slots claimed\n"
        f"▓ **Total Fill:** {overall_bar}\n\n"
        f"{Theme.THIN_SEP}\n\n"
        f"{groups_text}\n\n"
        f"{Theme.SEP}",
        Theme.PREMIUM,
        "🔄 Live updates │ Click Register to claim a slot"
    )
    return embed
