"""
Mack Bot — Embed Utilities
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


def build_registration_board_embed(groups=None, event_name="Daily Scrims"):
    """
    Build the permanent registration board embed for #register-here.
    This is the board that persists across days and gets reset at midnight.

    Uses circle-style progress bars: ●●●●●○○○○○

    Args:
        groups: Optional list of group documents. If None, shows empty board.
        event_name: Display name for the event
    """
    total_filled = 0
    total_capacity = 0
    group_lines = []

    if groups:
        for g in groups:
            gid = g.get("group_id", "????")
            count = g.get("current_count", 0)
            cap = g.get("capacity", 21)
            total_filled += count
            total_capacity += cap

            circle_bar = Theme.slot_bar(count, cap, 10)
            m1 = g.get("match1", {})
            m2 = g.get("match2", {})
            m1_map = m1.get("map", "TBD")
            m1_start = m1.get("start", "TBD")
            m2_map = m2.get("map", "TBD")
            m2_start = m2.get("start", "TBD")

            shift = g.get("shift", "")
            shift_emoji = "☀️" if shift == "day" else "🌙" if shift == "evening" else "📍"

            group_lines.append(
                f"{shift_emoji} **Group {gid}** — `{count}/{cap}` {circle_bar}\n"
                f"   ⏱ M1: `{m1_start}` ({m1_map}) │ M2: `{m2_start}` ({m2_map})"
            )
    else:
        total_capacity = 1  # Avoid division by zero

    overall_bar = Theme.slot_bar(total_filled, total_capacity, 10)
    groups_text = "\n\n".join(group_lines) if group_lines else (
        "○○○○○○○○○○  `0/0 filled`\n\n*No groups provisioned yet. Check back later!*"
    )

    embed = make_embed(
        f"📋 {event_name} — Registration Board",
        f"{Theme.SEP}\n\n"
        f"📊 **Slots Available:** `{total_filled}/{total_capacity}` filled\n"
        f"▓ **Overall:** {overall_bar}\n\n"
        f"{Theme.THIN_SEP}\n\n"
        f"{groups_text}\n\n"
        f"{Theme.THIN_SEP}\n"
        f"🔘 Click **📥 Register Team** below to claim a slot!\n\n"
        f"{Theme.SEP}",
        Theme.PREMIUM,
        "🔄 Auto-updates on every registration"
    )
    return embed


def build_registration_receipt_embed(team_name, group_id, players,
                                      player_uids, player_igns,
                                      members, date_display):
    """
    Build a public receipt embed for #registered-teams log channel.

    Args:
        team_name: Team display name
        group_id: Assigned group ID
        players: List of player names
        player_uids: List of player UIDs
        player_igns: List of player IGNs
        members: List of Discord members
        date_display: Formatted date string
    """
    # Build roster display
    if player_uids and player_igns:
        roster_lines = [
            f"  │  `{player_uids[i]}` — {player_igns[i]}"
            for i in range(min(len(player_uids), len(player_igns)))
        ]
    else:
        roster_lines = [f"  │  ✦ {p}" for p in players]

    roster_text = "\n".join(roster_lines)

    # Build members display
    member_mentions = " ".join([m.mention for m in members]) if members else "N/A"

    embed = make_embed(
        f"✅ Team Registered — {team_name}",
        f"╭── 📋 **Registration Receipt** ──╮\n"
        f"│  🏷️ **Team:** `{team_name}`\n"
        f"│  📍 **Group:** `{group_id}`\n"
        f"│  📅 **Date:** `{date_display}`\n"
        f"│\n"
        f"│  👥 **Roster:**\n{roster_text}\n"
        f"│\n"
        f"│  🎮 **Discord Members:**\n"
        f"│  {member_mentions}\n"
        f"╰────────────────────────────────╯",
        Theme.SUCCESS,
        "Mack Bot — Registration Log"
    )
    return embed


def build_group_control_panel_embed(group_doc):
    """
    Build the Group Control Panel embed for group channels.

    Args:
        group_doc: The group document from MongoDB
    """
    group_id = group_doc.get("group_id", "????")
    m1 = group_doc.get("match1", {})
    m2 = group_doc.get("match2", {})
    count = group_doc.get("current_count", 0)
    cap = group_doc.get("capacity", 21)

    embed = make_embed(
        f"⚙️ Group {group_id} — Control Panel",
        f"{Theme.SEP}\n\n"
        f"╭── 🎮 **Match Info** ──╮\n"
        f"│  **M1:** `{m1.get('start', 'TBD')}` │ IDP `{m1.get('idp', 'TBD')}` │ `{m1.get('map', 'TBD')}`\n"
        f"│  **M2:** `{m2.get('start', 'TBD')}` │ IDP `{m2.get('idp', 'TBD')}` │ `{m2.get('map', 'TBD')}`\n"
        f"╰────────────────────────────╯\n\n"
        f"📊 **Slots:** `{count}/{cap}` │ {Theme.bar(count, cap, 10)}\n\n"
        f"Use the buttons below to manage this group.\n\n"
        f"**Row 1** — Admin Only\n"
        f"**Row 2** — Teams & Admins\n"
        f"**Row 3** — Admin Only\n\n"
        f"{Theme.SEP}",
        Theme.PREMIUM,
        f"Group {group_id} Panel │ Mack Bot"
    )
    return embed
