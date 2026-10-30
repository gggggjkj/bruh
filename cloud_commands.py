"""Keep local-only command choices visible with a cloud availability message."""
import discord
from discord import app_commands

CLOUD_MESSAGE = "現在為雲端板無法使用該功能"


def install(bot):
    tree = app_commands.CommandTree(bot)
    @tree.command(name="生成", description="本機功能（目前為雲端版，暫無法使用）")
    @app_commands.guild_only()
    @app_commands.describe(模型="選擇功能", prompt="輸入內容")
    @app_commands.choices(模型=[app_commands.Choice(name="詢問雙子星大人一次", value="text"),
                                app_commands.Choice(name="生圖", value="image")])
    async def one_shot(interaction: discord.Interaction, 模型: app_commands.Choice[str], prompt: app_commands.Range[str, 1, 4000]):
        await interaction.response.send_message(CLOUD_MESSAGE, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
    return tree
