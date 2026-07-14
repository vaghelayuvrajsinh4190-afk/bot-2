"""
Mack Bot — Updater Utility
Centralized async utility to handle Discord message updates for registration and rosters.
Includes caching to prevent rate limits and duplicate edits.
"""

import asyncio
import hashlib
import json
import logging
import discord

from database import get_config, get_channel_config
from models import group as group_model, registration as reg_model
from utils.embeds import build_roster_embed, build_registration_board_embed

from collections import OrderedDict

# In-memory cache for embed hashes: { message_id: md5_hash }
# Used to prevent unnecessary Discord API edit calls.
# Bounded to 1000 entries to prevent memory leaks.
_embed_hash_cache = OrderedDict()
MAX_CACHE_SIZE = 1000

def _cache_set(msg_id: int, val: str):
    _embed_hash_cache[msg_id] = val
    if len(_embed_hash_cache) > MAX_CACHE_SIZE:
        _embed_hash_cache.popitem(last=False)

def get_embed_hash(embed: discord.Embed) -> str:
    """Generate a stable hash of the embed content, ignoring timestamps."""
    embed_dict = embed.to_dict()
    # Timestamps change on every generation, ignore for hashing
    if 'timestamp' in embed_dict:
        del embed_dict['timestamp']
    dumped = json.dumps(embed_dict, sort_keys=True)
    return hashlib.md5(dumped.encode('utf-8')).hexdigest()

async def safe_edit_message(channel: discord.TextChannel, msg_id: int, embed: discord.Embed, fallback_send: bool = True) -> int:
    """
    Safely edits a message. Checks cache to avoid redundant edits.
    Uses get_partial_message to save an API call where possible.
    Returns the message ID (old one, or newly sent one if it was deleted).
    """
    new_hash = get_embed_hash(embed)
    
    # If the message content hasn't changed, skip the API call
    if _embed_hash_cache.get(msg_id) == new_hash:
        return msg_id

    try:
        partial_msg = channel.get_partial_message(msg_id)
        await partial_msg.edit(embed=embed)
        _cache_set(msg_id, new_hash)
        return msg_id
    except discord.NotFound:
        # Message was deleted
        if fallback_send:
            try:
                new_msg = await channel.send(embed=embed)
                _cache_set(new_msg.id, new_hash)
                return new_msg.id
            except discord.HTTPException as e:
                logging.error(f"Failed to send fallback message in {channel.name}: {e}")
                return None
    except discord.HTTPException as e:
        logging.error(f"Failed to edit message {msg_id} in {channel.name}: {e}")
        
    return msg_id

async def update_group_roster(guild: discord.Guild, event_id: str, group_id: str):
    """Update the live roster embed in the specific group channel."""
    # Run DB calls in thread
    group_doc = await asyncio.to_thread(group_model.get_group, event_id, group_id)
    if not group_doc:
        return

    channel_id = group_doc.get("channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    regs = await asyncio.to_thread(reg_model.get_group_registrations, group_id, event_id)
    
    # Embed building should be fast, can run in async context
    embed = build_roster_embed(group_doc, regs, group_doc.get("capacity", 21))
    
    msg_id = group_doc.get("roster_message_id")
    
    if msg_id:
        new_msg_id = await safe_edit_message(channel, msg_id, embed, fallback_send=True)
        if new_msg_id and new_msg_id != msg_id:
            await asyncio.to_thread(group_model.update_roster_message, event_id, group_id, new_msg_id)
    else:
        # Send a new one
        try:
            new_msg = await channel.send(embed=embed)
            _cache_set(new_msg.id, get_embed_hash(embed))
            await asyncio.to_thread(group_model.update_roster_message, event_id, group_id, new_msg.id)
        except discord.HTTPException as e:
            logging.error(f"Failed to send initial roster msg in {channel.name}: {e}")

async def update_registration_board(guild: discord.Guild, event_id: str):
    """Update the slot availability embed in the register channel."""
    reg_channel_id = await asyncio.to_thread(get_channel_config, "register")
    if not reg_channel_id:
        return

    channel = guild.get_channel(reg_channel_id)
    if not channel:
        return

    all_groups = await asyncio.to_thread(group_model.get_all_groups, event_id)
    embed = build_registration_board_embed(all_groups)
    
    # Check permanent board first
    slot_msg_id = await asyncio.to_thread(get_config, "slot_message_id")
    if slot_msg_id:
        await safe_edit_message(channel, slot_msg_id, embed, fallback_send=False)
        return

    # Check event-specific board (fallback)
    avail_msg_id = await asyncio.to_thread(get_config, f"slot_availability_msg_{event_id}")
    if avail_msg_id:
        await safe_edit_message(channel, avail_msg_id, embed, fallback_send=False)
