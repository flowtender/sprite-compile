import discord
from discord import app_commands
from discord.ext import commands


@app_commands.command(description="Check that the bot answers")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("pong")


async def setup(bot: commands.Bot) -> None:
    bot.emojis_at_extension_load = bot.app_emojis
    bot.tree.add_command(ping)
