from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    raise RuntimeError("Duplicate command: group=None name='ping'")
