"""
Mack Bot — Embed Utilities
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Centralized embed builder and visual helpers for the Mack Bot UI.

Implements a Tier-1 Esports visual language with consistent theming,
premium colour gradients, and structured data presentation across all
bot responses.

Provides:
    make_embed / error_embed / success_embed   —  Base embed factories
    build_roster_embed                          —  Group roster cards
    build_registration_board_embed              —  Slot availability boards
    build_registration_receipt_embed            —  Confirmation receipts
    build_group_control_panel_embed             —  Admin control panels
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
    Uses 16-slot visual layout with 🟢/⚪ status indicators.

    Args:
        group_doc: The group document from MongoDB
        registrations: List of registration documents for this group
        capacity: Max teams in the group
    """
    group_id = group_doc.get("group_id", "????")
    count = group_doc.get("current_count", 0)
    reserved_count = group_doc.get("reserved_slots", 0)
    # Public counts exclude reserved slots for accurate display
    public_count = max(0, count - reserved_count)
    public_capacity = capacity - reserved_count
    # Build display name using scrim_id and group number
    scrim_id = group_doc.get("scrim_id", "SQ")
    try:
        grp_num = int(group_id.lstrip("G"))
    except (ValueError, AttributeError):
        grp_num = 0
    display_name = f"[{scrim_id.upper()}] GRP-{grp_num:02d}"
    status = Theme.group_status(count, capacity)
    color = Theme.group_color(count, capacity)
    bar = Theme.circle_bar(public_count, public_capacity, 16)

    # Build the match info
    match1 = group_doc.get("match1", {})
    match2 = group_doc.get("match2", {})
    m1_idp = match1.get("idp", "TBD")
    m1_start = match1.get("start", "TBD")
    m1_map = match1.get("map", "TBD")
    m2_idp = match2.get("idp", "TBD")
    m2_start = match2.get("start", "TBD")
    m2_map = match2.get("map", "TBD")

    reserved_slots = group_doc.get("reserved_slots", 0)
    reserved_teams = group_doc.get("reserved_teams", {})

    # Map registrations by their assigned slot number
    reg_by_slot = {r.get("slot_number"): r for r in registrations if r.get("slot_number") is not None}

    # Build roster lines — each slot maps directly to its assigned team
    slot_lines = []
    for slot_num in range(1, capacity + 1):
        num = f"{slot_num:02d}"
        if slot_num <= reserved_slots:
            # Reserved slot
            if str(slot_num) in reserved_teams:
                team_name = reserved_teams[str(slot_num)]
                team_name = (team_name[:18] + '..') if len(team_name) > 18 else team_name
                slot_lines.append(f"`{num}` 🟢 **{team_name}** › *Reserved*")
            else:
                slot_lines.append(f"`{num}` 🔴 **RESERVED**")
        elif slot_num in reg_by_slot:
            # Public registration with assigned slot
            reg = reg_by_slot[slot_num]
            tn = reg.get("team_name", "Unknown")
            tn = (tn[:18] + '..') if len(tn) > 18 else tn
            captain = reg.get("owner_id", "")
            slot_lines.append(f"`{num}` 🟢 **{tn}** › <@{captain}>")
        else:
            # Open slot
            slot_lines.append(f"`{num}` ⚪ *Available*")

    # Split into two columns if capacity > 8
    if capacity > 8:
        mid = (capacity + 1) // 2
        col1 = "\n".join(slot_lines[:mid])
        col2 = "\n".join(slot_lines[mid:])
    else:
        col1 = "\n".join(slot_lines)
        col2 = None

    embed = make_embed(
        f"🏆  {display_name}  ─  Live Roster",
        f"📡 **Status:** {status}\n"
        f"📊 **Slots:** **{public_count}/{public_capacity}** Filled"
        f"{f' │ 🔒 **{reserved_count}** Reserved' if reserved_count else ''}\n"
        f"▓ **Roster Fill:** {bar}\n\n"
        f"> **Match 1:** `{m1_start}` ─ `{m1_map}`\n"
        f"> **Match 2:** `{m2_start}` ─ `{m2_map}`",
        color=color,
        footer="🔄 Auto-updates │ Scrims"
    )

    # Add roster columns
    embed.add_field(name="📋 **Registered Squads**", value=col1 or "*No slots*", inline=bool(col2))
    if col2:
        embed.add_field(name="\u200b", value=col2, inline=True)

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
        reserved = g.get("reserved_slots", 0)
        # Public counts exclude reserved slots
        pub_count = max(0, count - reserved)
        pub_cap = cap - reserved
        total_filled += pub_count
        total_capacity += pub_cap

        status = Theme.group_status(count, cap)
        bar = Theme.circle_bar(pub_count, pub_cap, 10)

        m1 = g.get("match1", {})
        m2 = g.get("match2", {})
        m1_start = m1.get("start", "TBD")
        m2_start = m2.get("start", "TBD")

        # Build display name from scrim_id and group number
        scrim_id = g.get("scrim_id", "SQ")
        try:
            grp_num = int(gid.lstrip("G"))
        except (ValueError, AttributeError):
            grp_num = 0
        grp_display = f"[{scrim_id.upper()}] GRP-{grp_num:02d}"

        lines.append(
            f"**✦ {grp_display}** ── {status}\n"
            f"  {bar}  `{pub_count}/{pub_cap} filled`\n"
            f"  ⏱ **Matchtimes:** `{m1_start}` │ `{m2_start}`"
        )

    overall_bar = Theme.circle_bar(total_filled, total_capacity, 18)
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
    REGISTRATION PORTAL - tournament-style visual design.

    Args:
        groups: Optional list of group documents. If None, shows empty board.
        event_name: Display name for the event
    """
    from config import TIMEZONE_OFFSET

    # Get today's date in IST for "Group NNN - 2 Aug" display
    utc_now = datetime.datetime.utcnow()
    local_now = utc_now + datetime.timedelta(hours=TIMEZONE_OFFSET)
    try:
        date_display = local_now.strftime("%-d %b")
    except ValueError:
        # Windows doesn't support %-d, use %d and strip leading zero
        date_display = local_now.strftime("%d %b").lstrip("0")

    total_filled = 0
    total_capacity = 0
    group_lines = []

    if groups:
        for g in groups:
            gid = g.get("group_id", "????")
            count = g.get("current_count", 0)
            cap = g.get("capacity", 21)
            reserved = g.get("reserved_slots", 0)
            # Public counts exclude reserved slots
            pub_count = max(0, count - reserved)
            pub_cap = cap - reserved
            total_filled += pub_count
            total_capacity += pub_cap

            # Circle-dot progress bar
            dot_bar = Theme.circle_bar(pub_count, pub_cap, 12)

            # IDP-based timing instead of start times
            m1 = g.get("match1", {})
            m2 = g.get("match2", {})
            m1_idp = m1.get("idp", m1.get("start", "TBD"))
            m2_idp = m2.get("idp", m2.get("start", "TBD"))

            # Extract group number for "Group NNN - Date" format
            try:
                grp_num = int(gid.lstrip("G"))
            except (ValueError, AttributeError):
                grp_num = 0

            # Status emoji based on fill ratio
            if count >= cap:
                status_emoji = "🔴"
            elif count >= cap * 0.75:
                status_emoji = "🟡"
            else:
                status_emoji = "🟢"

            group_lines.append(
                f"{status_emoji} **Group {grp_num} \u2014 {date_display}**\n"
                f"🕒 **IDP:** M1: `{m1_idp}` | M2: `{m2_idp}`\n"
                f"`{dot_bar}` {pub_count}/{pub_cap} filled\n"
            )
    else:
        total_capacity = 1  # Avoid division by zero

    if group_lines:
        groups_text = "\n".join(group_lines)
    else:
        groups_text = (
            "`⚪⚪⚪⚪⚪⚪⚪⚪⚪⚪⚪⚪` 0/0 filled\n\n"
            "*No groups provisioned yet. Check back later!*"
        )

    instructions = (
        "Welcome to the official registration system for the upcoming qualifiers. "
        "Please read the instructions carefully before claiming your slot.\n\n"
        "**📝 How to Register:**\n"
        "**1.** Click the 📥 **Register Team** button below.\n"
        "**2.** Fill out the form with your Team Name and IGL details.\n"
        "**3.** Make sure your in-game Character IDs are ready.\n"
        "**4.** Submit to instantly lock in your slot.\n\n"
        "**⚠️ Important Rules:**\n"
        "• Slots are strictly **first-come, first-served**.\n"
        "• Spamming the form will result in a server ban.\n"
        "• Roster changes are not allowed after submission.\n\n"
        "**📊 Current Slot Status:**\n"
    )

    description = instructions + groups_text

    embed = discord.Embed(
        title="🏆 REGISTRATION PORTAL",
        description=description,
        color=discord.Color.from_rgb(230, 81, 0),
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_footer(text="Secure your slot now")
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
    scrim_id = group_doc.get("scrim_id", "SQ")
    try:
        grp_num = int(group_id.lstrip("G"))
    except (ValueError, AttributeError):
        grp_num = 0
    grp_display = f"[{scrim_id.upper()}] GRP-{grp_num:02d}"
    m1 = group_doc.get("match1", {})
    m2 = group_doc.get("match2", {})
    count = group_doc.get("current_count", 0)
    cap = group_doc.get("capacity", 21)
    reserved = group_doc.get("reserved_slots", 0)
    # Public counts exclude reserved slots
    pub_count = max(0, count - reserved)
    pub_cap = cap - reserved

    embed = make_embed(
        f"⚙️ {grp_display} — Control Panel",
        f"{Theme.SEP}\n\n"
        f"╭── 🎮 **Match Info** ──╮\n"
        f"│  **M1:** `{m1.get('start', 'TBD')}` │ IDP `{m1.get('idp', 'TBD')}` │ `{m1.get('map', 'TBD')}`\n"
        f"│  **M2:** `{m2.get('start', 'TBD')}` │ IDP `{m2.get('idp', 'TBD')}` │ `{m2.get('map', 'TBD')}`\n"
        f"╰────────────────────────────╯\n\n"
        f"📊 **Slots:** `{pub_count}/{pub_cap}` │ {Theme.circle_bar(pub_count, pub_cap, 10)}"
        f"{f' │ 🔒 {reserved} Reserved' if reserved else ''}\n\n"
        f"Use the buttons below to manage this group.\n\n"
        f"**Row 1** — Admin Only\n"
        f"**Row 2** — Teams & Admins\n"
        f"**Row 3** — Admin Only\n\n"
        f"{Theme.SEP}",
        Theme.PREMIUM,
        f"{grp_display} Panel │ Mack Bot"
    )
    return embed


def build_provision_summary_embed(event_id, created_count, capacity,
                                   category_name, provisioned_by=None):
    """
    Build a premium provisioning summary embed.

    Args:
        event_id: The event date ID
        created_count: Number of groups created
        capacity: Teams per group
        category_name: Name of the created category
        provisioned_by: Display name of the admin who triggered it
    """
    total_slots = capacity * created_count

    embed = make_embed(
        "✅ Provisioning Complete!",
        f"## ⚡ GROUPS DEPLOYED SUCCESSFULLY ⚡\n\n"
        f"> 📅 **Event:** `{event_id}`\n"
        f"> 📂 **Category:** `{category_name}`\n"
        f"> 👥 **Groups:** `{created_count}`\n"
        f"> 🏟️ **Capacity:** `{capacity}` per group\n"
        f"> 🎮 **Total Slots:** `{total_slots}`\n"
        f"> 📋 **Schedule:** Using `schedule.json`\n\n"
        f"Created: **{created_count}** channels, "
        f"**{created_count}** roles, "
        f"**1** registration channel\n\n"
        f"{Theme.SEP}",
        Theme.SUCCESS,
        f"Provisioned by {provisioned_by or 'Autopilot'} │ Scrim System"
    )
    return embed


def build_global_leaderboard_embed(teams, tier_filter=None):
    """
    Build the global leaderboard embed for a tier.
    """
    tier_label = f"[{tier.upper()}] " if tier else "Global "
    lines = []
    
    for i, t in enumerate(teams, 1):
        team_name = t.get("team_name", "Unknown")
        team_name = (team_name[:20] + '..') if len(team_name) > 20 else team_name
        pts = t.get("total_points", 0)
        kills = t.get("total_kills", 0)
        matches = t.get("matches_played", 0)
        wins = t.get("wins", 0)
        
        # Determine medal
        medal = "🏅"
        if i == 1: medal = "🥇"
        elif i == 2: medal = "🥈"
        elif i == 3: medal = "🥉"
        
        # Calculate KD (kills per match)
        kd = round(kills / matches, 1) if matches > 0 else 0.0
        
        lines.append(
            f"`{i:02d}` {medal} **{team_name}**\n"
            f"       🏆 `{pts}` pts │ 💀 `{kills}` kills ({kd} K/M) │ 🍗 `{wins}` wins"
        )
        
    board_text = "\n\n".join(lines) if lines else "*No teams have recorded points yet.*"
    
    embed = make_embed(
        f"🌐 {tier_label}Leaderboard",
        f"{Theme.SEP}\n\n{board_text}\n\n{Theme.SEP}",
        Theme.PREMIUM,
        f"Top {len(teams)} Teams │ Cross-Tier Tracking"
    )
    return embed


def build_team_stats_embed(team_doc):
    """
    Build a detailed stats card for a single team globally.
    """
    team_name = team_doc.get("team_name", "Unknown")
    owner_id = team_doc.get("owner_id", "")
    tier = team_doc.get("current_tier", "T3")
    
    pts = team_doc.get("total_points", 0)
    kills = team_doc.get("total_kills", 0)
    matches = team_doc.get("matches_played", 0)
    wins = team_doc.get("wins", 0)
    no_shows = team_doc.get("no_shows", 0)
    
    kd = round(kills / matches, 1) if matches > 0 else 0.0
    win_rate = round((wins / matches) * 100, 1) if matches > 0 else 0.0
    
    embed = make_embed(
        f"📊 Team Profile: {team_name}",
        f"**Owner:** <@{owner_id}>\n"
        f"**Current Tier:** `[{tier}]`\n\n"
        f"╭── 📈 **Global Statistics** ──╮\n"
        f"│  🏆 **Total Points:** `{pts}`\n"
        f"│  💀 **Total Kills:** `{kills}`\n"
        f"│  ⚔️ **Matches Played:** `{matches}`\n"
        f"│  🍗 **Match Wins:** `{wins}`\n"
        f"╰─────────────────────────╯\n\n"
        f"**Averages:** `{kd}` K/M │ `{win_rate}%` Win Rate\n"
        f"**Strikes (No Shows):** `{no_shows}`\n\n"
        f"{Theme.SEP}",
        Theme.INFO,
        "Mack Bot Global Tracking"
    )
    return embed
