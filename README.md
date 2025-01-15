# **Discord Mass DM Bot**

A powerful Discord bot for sending mass DMs with advanced rate-limiting, emoji support, and safety features. Built using `discord.py`.

## **Features**
- 🚀 Send DMs to all server members with advanced rate-limiting.
- 💬 Copy and send messages using a message ID or link.
- 😄 Full support for custom and animated emojis.
- ⚡ Efficient chunking system for large servers (5k+ members).
- 📊 Real-time progress tracking with a visual progress bar.
- 🔄 Auto-retry for rate limits.
- 📝 Detailed logging for all actions.
- 🛡️ Built-in safety measures and authorization checks.

## **Installation**

1. Clone the repository from GitHub.
2. Install required dependencies using `pip install`.
3. Create a `.env` file with your bot token:
```bash
DISCORD_TOKEN=your_token_here
```
4. Run the bot script.

## **Commands**

- `/dmall` - Send a custom embed message to all server members.
- `/dmallmessageid` - Copy and send a message using its ID or link.
- `/reloademojis` - Refresh the server's emoji catalog.

## **Rate Limiting**

The bot includes advanced rate-limiting mechanisms:
- **Global Limit**: 50 messages per second.
- **Per-User Cooldown**: 5 seconds.
- **Chunk Size**: Processes 1,000 members per chunk.
- **Random Delays**: Adds a 100–300ms delay between messages.
- **Chunk Delay**: Waits 5 seconds between chunks.

## **Logging**

- **Console Output**: Real-time updates.
- **Log Files**:
- `bot.log` for detailed logs.
- `dmblast_log.txt` for DM blast statistics.

## **Security**

- 🔒 **User ID Verification**: Ensures only authorized users can execute commands.
- 🛡️ **Emoji Safety**: Handles emojis securely to prevent issues.
- ⚙️ **Error Reporting**: Catches and reports errors during execution.
- 👀 **Progress Monitoring**: Tracks progress for transparency.

## **License**

This project is licensed under the [MIT License](LICENSE).

## **Disclaimer**

**Use this bot responsibly** and adhere to [Discord's Terms of Service](https://discord.com/terms). Mass DM features should only be used for legitimate purposes, such as server announcements or critical updates.
