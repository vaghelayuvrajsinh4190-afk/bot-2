"""
Mack Bot — Permission Utilities
Helper functions for managing Discord roles and channel permissions.
"""

import discord


async def get_or_create_role(guild, role_name, color=None):
    """Get an existing role by name, or create it if it doesn't exist."""
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        try:
            role = await guild.create_role(
                name=role_name,
                mentionable=True,
                color=color or discord.Color.default()
            )
        except discord.Forbidden:
            return None
        except Exception:
            return None
    return role


async def create_group_channel(guild, category, channel_name, group_role, admin_roles=None):
    """
    Create a text channel locked to a specific group role + admins.
    
    Args:
        guild: The Discord guild
        category: The category to create the channel in
        channel_name: Name for the channel
        group_role: The role that gets access
        admin_roles: Optional list of admin roles that also get access
    """
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        group_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            read_message_history=True,
            manage_messages=True
        )
    }

    # Add admin role access
    if admin_roles:
        for admin_role in admin_roles:
            overwrites[admin_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True
            )

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites
        )
        return channel
    except discord.Forbidden:
        return None
    except Exception:
        return None


async def create_day_category(guild, category_name):
    """Create a category for the day's groups."""
    try:
        category = await guild.create_category(name=category_name)
        return category
    except discord.Forbidden:
        return None
    except Exception:
        return None


async def cleanup_channel(guild, channel_id):
    """Delete a channel by ID, ignoring errors if it's already gone."""
    try:
        channel = guild.get_channel(channel_id)
        if channel:
            await channel.delete(reason="Nightly cleanup")
            return True
    except (discord.NotFound, discord.Forbidden):
        pass
    except Exception:
        pass
    return False


async def cleanup_role(guild, role_id):
    """Delete a role by ID, ignoring errors if it's already gone."""
    try:
        role = guild.get_role(role_id)
        if role:
            await role.delete(reason="Nightly cleanup")
            return True
    except (discord.NotFound, discord.Forbidden):
        pass
    except Exception:
        pass
    return False


async def cleanup_category(guild, category_id):
    """Delete a category by ID, ignoring errors."""
    try:
        category = guild.get_channel(category_id)
        if category:
            await category.delete(reason="Nightly cleanup")
            return True
    except (discord.NotFound, discord.Forbidden):
        pass
    except Exception:
        pass
    return False


async def grant_group_access(member, role):
    """Give a member access to a group by assigning the group role."""
    try:
        await member.add_roles(role)
        return True
    except (discord.Forbidden, Exception):
        return False


async def revoke_group_access(member, role):
    """Remove a member's group role."""
    try:
        await member.remove_roles(role)
        return True
    except (discord.Forbidden, Exception):
        return False
