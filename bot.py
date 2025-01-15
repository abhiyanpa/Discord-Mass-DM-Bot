import discord
from discord import File
from discord.ext import commands
from dotenv import load_dotenv
import os
import asyncio
import re
import logging
import random
import concurrent.futures
from itertools import islice
import time
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

def create_progress_bar(current, total, length=20):
   """
   Creates a fancy progress bar with unicode blocks
   """
   filled = int(length * current / total)
   fill_char = "█"
   empty_char = "░"
   percentage = (current / total * 100)
   bar = fill_char * filled + empty_char * (length - filled)
   return f"Progress: {bar} | {current}/{total} ({percentage:.1f}%)"

class RateLimitHandler:
   def __init__(self):
       self.message_queue = asyncio.Queue()
       self.global_semaphore = asyncio.Semaphore(40)  # Reduced from 50 to be safer
       self.user_semaphores = {}
       self.last_messages = {}
       self.workers = 5  # Reduced from 10 to be more conservative
       self.daily_limit = 3000  # Daily limit per bot
       self.current_count = 0
       self.reset_time = time.time() + 86400  # 24 hours

   async def process_member(self, member, content=None, embed=None):
       try:
           if member != bot.user:
               # Check daily limit
               if self.current_count >= self.daily_limit:
                   if time.time() > self.reset_time:
                       self.current_count = 0
                       self.reset_time = time.time() + 86400
                   else:
                       return False

               # Check rate limits
               current_time = time.time()
               if member.id in self.last_messages:
                   time_diff = current_time - self.last_messages[member.id]
                   if time_diff < 1:  # Minimum 1 second between messages to same user
                       await asyncio.sleep(1 - time_diff)

               async with self.global_semaphore:
                   # Add some randomization to message timing
                   await asyncio.sleep(random.uniform(0.1, 0.3))
                   
                   if content:
                       await member.send(content=content)
                   if embed:
                       await member.send(embed=embed)
                   
                   self.last_messages[member.id] = time.time()
                   self.current_count += 1
                   
                   # Random delay between messages
                   await asyncio.sleep(random.uniform(0.2, 0.5))
                   return True

       except discord.Forbidden:
           logging.warning(f"Cannot send DM to {member} - DMs disabled")
       except discord.HTTPException as e:
           if e.code == 429:  # Rate limit hit
               retry_after = float(e.response.headers.get('Retry-After', 5))
               logging.warning(f"Rate limited, waiting {retry_after} seconds")
               await asyncio.sleep(retry_after)
               return False
           logging.error(f"HTTP Exception for {member}: {e}")
           return False
       except Exception as e:
           logging.error(f"Error sending DM to {member}: {e}")
       return False

   async def process_chunk(self, members, content=None, embed=None):
       tasks = []
       for member in members:
           task = asyncio.create_task(self.process_member(member, content, embed))
           tasks.append(task)
           if len(tasks) >= self.workers:
               await asyncio.gather(*tasks)
               tasks = []
       if tasks:
           await asyncio.gather(*tasks)

class EmojiStore:
   def __init__(self):
       self.emojis = []  # Changed from set to list
       self.emoji_objects = {}

   def add_emoji(self, emoji):
       if hasattr(emoji, 'id'):
           emoji_str = f'<{"a:" if emoji.animated else ":"}{emoji.name}:{emoji.id}>'
           if emoji_str not in self.emojis:  # Check for duplicates
               self.emojis.append(emoji_str)
           self.emoji_objects[str(emoji.id)] = emoji

   def add_from_message(self, content):
       if not content:
           return
           
       emoji_pattern = r'<(a?):([^:]+):(\d+)>'
       emoji_matches = re.finditer(emoji_pattern, content)
       
       for match in emoji_matches:
           emoji_str = match.group(0)
           if emoji_str not in self.emojis:
               self.emojis.append(emoji_str)

   def clear(self):
       self.emojis = []
       self.emoji_objects.clear()

emoji_store = EmojiStore()

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
       start_time = time.time()
       last_progress_update = time.time()

       # Process in smaller chunks
       chunk_size = 50  # Reduced chunk size
       members = [m for m in interaction.guild.members if not m.bot]
       total_members = len(members)
       
       for i in range(0, total_members, chunk_size):
           chunk = members[i:i + chunk_size]
           chunk_start = time.time()
           
           tasks = []
           for member in chunk:
               task = asyncio.create_task(rate_limiter.process_member(member, content, embed))
               tasks.append(task)
           
           results = await asyncio.gather(*tasks, return_exceptions=True)
           success_count += sum(1 for r in results if r is True)
           failed_count += sum(1 for r in results if r is False)

           # Update progress every 2 seconds
           current_time = time.time()
           if current_time - last_progress_update >= 2:
               elapsed = time.time() - start_time
               speed = (i + len(chunk)) / elapsed if elapsed > 0 else 0
               eta = (total_members - (i + len(chunk))) / speed if speed > 0 else 0
               
               progress_text = create_progress_bar(i + len(chunk), total_members)
               stats = f"\nSuccess: {success_count} | Failed: {failed_count}"
               speed_text = f"\nSpeed: {speed:.1f} DMs/sec | ETA: {eta/60:.1f} minutes"
               
               try:
                   if not progress_message:
                       progress_message = await interaction.followup.send(
                           progress_text + stats + speed_text)
                   else:
                       await progress_message.edit(content=
                           progress_text + stats + speed_text)
                   last_progress_update = current_time
               except:
                   continue

           # Random delay between chunks
           await asyncio.sleep(random.uniform(0.5, 1.0))

       total_time = time.time() - start_time
       log_message = (
           f"DM blast completed in {total_time/60:.1f} minutes.\n"
           f"Successful: {success_count}\nFailed: {failed_count}\n"
           f"Total: {total_members}\n"
           f"Average Speed: {total_members/total_time:.1f} DMs/sec"
       )
       
       try:
           await interaction.followup.send(log_message)
       except:
           logging.info(log_message)

       with open("dmblast_log.txt", "a") as log_file:
           log_file.write(f"[{interaction.created_at}] {interaction.user} - {log_message}\n")
   else:
       await interaction.edit_original_response(content="DM blast cancelled.", view=None)

async def fetch_all_emojis():
   emoji_store.clear()

   for guild in bot.guilds:
       try:
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
       color = int(color, 16)
   except ValueError:
       color = discord.Color.default()

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
       if 'discord.com/channels/' in message_link_or_id:
           parts = message_link_or_id.split('/')
           guild_id = int(parts[-3])
           channel_id = int(parts[-2])
           message_id = int(parts[-1])
       else:
           guild_id = interaction.guild_id
           channel_id = interaction.channel_id
           message_id = int(message_link_or_id)

       channel = bot.get_channel(channel_id)
       if not channel:
           channel = await bot.fetch_channel(channel_id)
       
       message = await channel.fetch_message(message_id)
       
       if message.content:
           content = message.content
       else:
           content = None

       if message.embeds:
           embed = message.embeds[0]
       else:
           embed = None

       if not content and not embed:
           await interaction.response.send_message("Message has no content or embed to send.", ephemeral=True)
           return

       await send_mass_dm(interaction, embed=embed, content=content)

   except discord.NotFound:
       await interaction.response.send_message("Message not found. Please check the ID/link and try again.", ephemeral=True)
   except (ValueError, IndexError):
       await interaction.response.send_message("Invalid message link or ID format. Please try again.", ephemeral=True)
   except Exception as e:
       await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)

bot.run(TOKEN)