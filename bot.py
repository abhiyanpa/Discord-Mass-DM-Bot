import discord
from discord import File
from discord.ext import commands
from dotenv import load_dotenv
import os
import asyncio
import re
import logging
import random
from asyncio import Queue, Semaphore
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

class RateLimitHandler:
    def __init__(self):
        self.message_queue = Queue()
        self.global_semaphore = Semaphore(50)  # 50 messages per second global
        self.user_semaphores = {}
        self.last_messages = {}
        self.daily_dm_count = 0
        self.daily_reset_time = datetime.now()
        
    async def add_to_queue(self, member, content=None, embed=None):
        await self.message_queue.put((member, content, embed))
        
    async def process_queue(self):
        if self.message_queue.empty():
            return True
            
        member, content, embed = await self.message_queue.get()
        
        # Check daily limit
        current_time = datetime.now()
        if current_time - self.daily_reset_time > timedelta(days=1):
            self.daily_dm_count = 0
            self.daily_reset_time = current_time
        
        if self.daily_dm_count >= 4900:  # Leave some buffer
            logging.warning("Daily DM limit approaching, pausing for 24 hours")
            return False
            
        # Get or create user semaphore
        if member.id not in self.user_semaphores:
            self.user_semaphores[member.id] = Semaphore(1)
            
        try:
            # Acquire both global and user-specific rate limit
            async with self.global_semaphore:
                async with self.user_semaphores[member.id]:
                    # Check user's last message time
                    last_time = self.last_messages.get(member.id, 0)
                    if datetime.now().timestamp() - last_time < 5:  # 5 second cooldown per user
                        await asyncio.sleep(5)
                        
                    try:
                        if content:
                            await member.send(content=content)
                        if embed:
                            await member.send(embed=embed)
                        
                        self.last_messages[member.id] = datetime.now().timestamp()
                        self.daily_dm_count += 1
                        
                        # Add random delay between messages (100-300ms)
                        await asyncio.sleep(random.uniform(0.1, 0.3))
                        
                        return True
                        
                    except discord.Forbidden:
                        logging.warning(f"Cannot send DM to {member} - DMs disabled")
                        return False
                    except discord.HTTPException as e:
                        if e.code == 50007:  # Cannot send messages to this user
                            logging.warning(f"Cannot send DM to {member} - {e}")
                            return False
                        elif e.status == 429:  # Rate limited
                            retry_after = e.retry_after
                            logging.warning(f"Rate limited. Waiting {retry_after} seconds")
                            await asyncio.sleep(retry_after)
                            return await self.process_queue()  # Retry
                        else:
                            logging.error(f"Error sending DM to {member}: {e}")
                            return False
                            
        except Exception as e:
            logging.error(f"Unexpected error processing queue: {e}")
            return False

class EmojiStore:
    def __init__(self):
        self.emojis = set()  # Store unique emoji strings
        self.emoji_objects = {}  # Store emoji objects by ID

    def add_emoji(self, emoji):
        # For custom emojis (both static and animated)
        if hasattr(emoji, 'id'):
            emoji_str = f'<{"a:" if emoji.animated else ":"}{emoji.name}:{emoji.id}>'
            self.emojis.add(emoji_str)
            self.emoji_objects[str(emoji.id)] = emoji

    def add_from_message(self, content):
        if not content:
            return
            
        # Extract emoji patterns from message content
        emoji_pattern = r'<(a?):([^:]+):(\d+)>'
        emoji_matches = re.finditer(emoji_pattern, content)
        
        for match in emoji_matches:
            # Store the full emoji pattern
            emoji_str = match.group(0)  # The complete emoji string
            self.emojis.add(emoji_str)

emoji_store = EmojiStore()

def sanitize_text(text: str) -> str:
    """
    Preserve server emojis while sanitizing other text
    """
    sanitized_text = text
    
    # Temporarily replace all known server emojis
    for i, emoji in enumerate(emoji_store.emojis):
        sanitized_text = sanitized_text.replace(emoji, f"EMOJI_{i}")
    
    # Remove any other emoji-like patterns that aren't from our server
    sanitized_text = re.sub(r'<a?:.+?:\d+>', '', sanitized_text)
    
    # Restore our server emojis
    for i, emoji in enumerate(emoji_store.emojis):
        sanitized_text = sanitized_text.replace(f"EMOJI_{i}", emoji)
            
    return sanitized_text

class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, custom_id="confirm_dmblast")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()

async def send_mass_dm(interaction: discord.Interaction, embed=None, content=None):
    """
    Helper function to send mass DMs with rate limiting
    """
    confirm_embed = discord.Embed(
        title="Confirm DM Blast",
        description=f"Are you sure you want to send this DM blast to **{interaction.guild.member_count}** members?",
        color=discord.Color.orange()
    )
    
    view = ConfirmView()
    await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
    await view.wait()
    
    if view.value:  # Confirmed
        await interaction.edit_original_response(content="Starting DM blast...", view=None)

        rate_limiter = RateLimitHandler()
        success_count = 0
        failed_count = 0
        progress_message = None
        
        # Split members into chunks of 1000
        member_chunks = [list(interaction.guild.members)[i:i + 1000] for i in range(0, len(interaction.guild.members), 1000)]
        
        for chunk_index, chunk in enumerate(member_chunks):
            chunk_success = 0
            chunk_failed = 0
            
            # Add all members in chunk to queue
            for member in chunk:
                if member != bot.user:
                    await rate_limiter.add_to_queue(member, content, embed)
            
            # Process queue for this chunk
            while not rate_limiter.message_queue.empty():
                result = await rate_limiter.process_queue()
                if result:
                    chunk_success += 1
                else:
                    chunk_failed += 1
                    
                # Update progress
                current_total = (chunk_index * 1000) + chunk_success + chunk_failed
                if current_total % 10 == 0 or current_total == interaction.guild.member_count:
                    if not progress_message:
                        progress_message = await interaction.followup.send(
                            f"Progress: {current_total}/{interaction.guild.member_count}\n"
                            f"Current Chunk: {chunk_index + 1}/{len(member_chunks)}")
                    else:
                        await progress_message.edit(content=
                            f"Progress: {current_total}/{interaction.guild.member_count}\n"
                            f"Current Chunk: {chunk_index + 1}/{len(member_chunks)}")
            
            success_count += chunk_success
            failed_count += chunk_failed
            
            # Add delay between chunks
            if chunk_index < len(member_chunks) - 1:
                await asyncio.sleep(5)  # 5 second delay between chunks

        log_message = f"DM blast completed.\nSuccessful: {success_count}\nFailed: {failed_count}\nTotal: {interaction.guild.member_count}"
        await interaction.followup.send(log_message)

        with open("dmblast_log.txt", "a") as log_file:
            log_file.write(f"[{interaction.created_at}] {interaction.user} - {log_message}\n")
    else:  # Cancelled
        await interaction.edit_original_response(content="DM blast cancelled.", view=None)

async def fetch_all_emojis():
    """Fetch emojis from all available sources"""
    # Clear existing emojis to prevent duplicates on reload
    emoji_store.emojis.clear()
    emoji_store.emoji_objects.clear()

    # Fetch from all servers the bot is in
    for guild in bot.guilds:
        try:
            # Ensure we have the latest emoji list
            await guild.fetch_emojis()
            for emoji in guild.emojis:
                emoji_store.add_emoji(emoji)
            logging.info(f'Cataloged emojis from server: {guild.name}')
        except Exception as e:
            logging.error(f'Error fetching emojis from {guild.name}: {e}')

    logging.info(f'Total emojis cataloged: {len(emoji_store.emojis)}')

@bot.event
async def on_ready():
    logging.info(f'We have logged in as {bot.user}')
    await fetch_all_emojis()
    await bot.tree.sync()

@bot.tree.command(name="reloademojis", description="Reload and update the emoji catalog")
async def reloademojis(interaction: discord.Interaction):
    if interaction.user.id != 774638041515294760:
        await interaction.response.send_message("You are not authorized to use this command.")
        return

    await interaction.response.send_message("Reloading emojis...", ephemeral=True)
    await fetch_all_emojis()
    await interaction.followup.send(f"Emoji reload complete!\nTotal emojis: {len(emoji_store.emojis)}", ephemeral=True)

@bot.tree.command(name="dmall", description="DM all server members with a customizable embed")
async def dmall(interaction: discord.Interaction, title: str, description: str, color: str, footer: str,
                field1_name: str, field1_value: str, field2_name: str = None, field2_value: str = None,
                attachment: discord.Attachment = None):
    if interaction.user.id != 774638041515294760:
        await interaction.response.send_message("You are not authorized to use this command.")
        return

    try:
        color = int(color, 16)  # Convert color from hex string to integer
    except ValueError:
        color = discord.Color.default()  # Use default color if invalid hex string provided

    # Sanitize all text inputs
    title = sanitize_text(title)
    description = sanitize_text(description)
    footer = sanitize_text(footer)
    field1_name = sanitize_text(field1_name)
    field1_value = sanitize_text(field1_value)
    
    if field2_name is not None:
        field2_name = sanitize_text(field2_name)
    if field2_value is not None:
        field2_value = sanitize_text(field2_value)

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color(color)
    )
    embed.add_field(name=field1_name, value=field1_value, inline=False)
    
    if field2_name is not None and field2_value is not None:
        embed.add_field(name=field2_name, value=field2_value, inline=False)
        
    embed.set_footer(text=footer)

    if attachment:
        embed.set_image(url=attachment.url)

    await send_mass_dm(interaction, embed=embed)

@bot.tree.command(name="dmallmessageid", description="DM all server members with a message copied from message ID")
async def dmallmessageid(interaction: discord.Interaction, message_link_or_id: str):
    if interaction.user.id != 774638041515294760:
        await interaction.response.send_message("You are not authorized to use this command.")
        return

    try:
        # Handle both message links and IDs
        if 'discord.com/channels/' in message_link_or_id:
            # Extract IDs from message link
            parts = message_link_or_id.split('/')
            guild_id = int(parts[-3])
            channel_id = int(parts[-2])
            message_id = int(parts[-1])
        else:
            # Use current channel if only message ID is provided
            guild_id = interaction.guild_id
            channel_id = interaction.channel_id
            message_id = int(message_link_or_id)

        # Get the channel and message
        channel = bot.get_channel(channel_id)
        if not channel:
            channel = await bot.fetch_channel(channel_id)
        
        message = await channel.fetch_message(message_id)
        
        # Extract and store emojis from the message content
        if message.content:
            emoji_store.add_from_message(message.content)
            content = message.content  # Keep original content with emoji formats
        else:
            content = None

        if message.embeds:
            embed = message.embeds[0]
            # Keep original embed as is
            if embed.description:
                emoji_store.add_from_message(embed.description)
            if embed.title:
                emoji_store.add_from_message(embed.title)
            if embed.footer.text:
                emoji_store.add_from_message(embed.footer.text)
            for field in embed.fields:
                emoji_store.add_from_message(field.name)
                emoji_store.add_from_message(field.value)
        else:
            embed = None
            
        # If message has reactions, store those emojis too
        for reaction in message.reactions:
            if hasattr(reaction.emoji, 'id'):  # Custom emoji
                emoji_store.add_emoji(reaction.emoji)

        if not content and not embed:
            await interaction.response.send_message("Message has no content or embed to send.", ephemeral=True)
            return

        await send_mass_dm(interaction, embed=embed, content=content)

    except discord.NotFound:
        await interaction.response.send_message("Message not found. Please check the ID/link and try again.", ephemeral=True)
    except (ValueError, IndexError):
        await interaction.response.send_message("Invalid message link or ID format. Please try again.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)

bot.run(TOKEN)