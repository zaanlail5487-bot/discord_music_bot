import os
import imageio_ffmpeg
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
import json
import asyncio
from threading import Thread
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

# ==================== Render 防休眠假網頁伺服器 ====================
app = Flask('')

@app.route('/')
def home():
    return "Music Bot 正在運行中！"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ==================== 1. 初始化設定與權限 ====================
intents = discord.Intents.default()
intents.message_content = True  
bot = commands.Bot(command_prefix="!", intents=intents)

# YT-DLP 播放優化參數
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# JSON 永久數據庫設定
DB_FILE = "music_system_data.json"

# ----------------------------------------------------
# 核心儲存庫：記錄當前的臨時播放狀態
# ----------------------------------------------------
class GuildMusicState:
    def __init__(self):
        self.queue = []       # 當前臨時排隊歌曲清單
        self.volume = 0.5     # 預設音量
        self.current_title = "目前沒有播放任何歌曲"
        self.interaction_channel = None 

music_states = {}

def get_music_state(guild_id):
    if guild_id not in music_states:
        music_states[guild_id] = GuildMusicState()
    return music_states[guild_id]

# ----------------------------------------------------
# 全域永久資料庫讀寫 (包含 YouTube 級別永久歌單與音量)
# ----------------------------------------------------
global_db = {"guilds": {}, "playlists": {}}

def save_db_to_disk():
    """保存當前狀態與所有永久歌單到硬碟"""
    for guild_id, state in music_states.items():
        if str(guild_id) not in global_db["guilds"]:
            global_db["guilds"][str(guild_id)] = {}
        global_db["guilds"][str(guild_id)]["volume"] = state.volume
        
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(global_db, f, ensure_ascii=False, indent=4)

def load_db_from_disk():
    """從硬碟載入所有永久歌單與音量設定"""
    global global_db
    if not os.path.exists(DB_FILE):
        return
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            global_db = json.load(f)
        for guild_id_str, data in global_db.get("guilds", {}).items():
            state = get_music_state(int(guild_id_str))
            state.volume = data.get("volume", 0.5)
        print("💾 已成功從硬碟載入所有 YouTube 永久歌單與音量記憶！")
    except Exception as e:
        print(f"❌ 數據庫載入失敗: {e}")

# ----------------------------------------------------

@bot.event
async def on_ready():
    print(f'🤖 萬能音樂播放機器人已成功上線：{bot.user.name}')
    load_db_from_disk() 
    await bot.tree.sync()

# ---------------- 核心音樂管理邏輯 ----------------

def play_next(guild_id, voice_client):
    """核心：自動播放下一首"""
    state = get_music_state(guild_id)
    if not state.queue:
        state.current_title = "目前沒有播放任何歌曲"
        return

    song_info = state.queue.pop(0)
    state.current_title = song_info['title']

    raw_player = discord.FFmpegPCMAudio(song_info['url'], executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
    transformed_player = discord.PCMVolumeTransformer(raw_player, volume=state.volume)

    voice_client.play(
        transformed_player, 
        after=lambda e: bot.loop.call_soon_threadsafe(play_next, guild_id, voice_client)
    )

    if state.interaction_channel:
        msg = f"🎵 **自動續播下一首**：**{song_info['title']}**\n👤 點歌者：{song_info['user']}\n🎚️ 當前音量：`{int(state.volume * 100)}%`"
        asyncio.run_coroutine_threadsafe(state.interaction_channel.send(msg), bot.loop)

# ==================== 🌟 永久歌單功能區 ====================

@bot.tree.command(name="playlist_create", description="🆕 永久創立一本像 YouTube 一樣的專屬自訂歌單")
@app_commands.describe(name="請輸入您想建立的歌單名稱")
async def playlist_create(interaction: discord.Interaction, name: str):
    if name in global_db["playlists"]:
        await interaction.response.send_message(f"❌ 錯誤：已經有名為 `{name}` 的歌單了！", ephemeral=True)
        return
    global_db["playlists"][name] = []
    save_db_to_disk()
    await interaction.response.send_message(f"🎉 成功建立永久歌單：**`{name}`**！您現在可以使用 `/playlist_add` 往裡面加歌囉。", ephemeral=True)


@bot.tree.command(name="playlist_add", description="➕ 將指定歌曲永久收藏進您的自訂歌單中")
@app_commands.describe(playlist_name="想要加入哪一本歌單", search="輸入歌曲網址、Spotify連結或歌名關鍵字")
async def playlist_add(interaction: discord.Interaction, playlist_name: str, search: str):
    if playlist_name not in global_db["playlists"]:
        await interaction.response.send_message(f"❌ 錯誤：找不到名為 `{playlist_name}` 的歌單，請先使用 `/playlist_create` 建立它。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    if "://spotify.com" in search:
        try:
            search = (await asyncio.get_event_loop().run_in_executor(
                None, lambda: ytdl.extract_info(search.split('?')[0], download=False, process=False)
            )).get('title', '').replace('| Spotify', '').strip()
        except Exception:
            pass

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        
        if 'entries' in data and len(data['entries']) > 0:
            track_data = data['entries'][0]
        else:
            track_data = data
            
        url, title = track_data['url'], track_data['title']
    except Exception as e:
        await interaction.followup.send(f"❌ 搜尋歌曲失敗: {e}", ephemeral=True)
        return

    global_db["playlists"][playlist_name].append({"url": url, "title": title})
    save_db_to_disk() 
    await interaction.followup.send(f"⭐️ 成功將 **{title}** 永久收藏至歌單【`{playlist_name}`】！", ephemeral=True)


@bot.tree.command(name="playlist_view", description="📋 瀏覽現有的所有永久歌單與歌曲內容")
@app_commands.describe(playlist_name="可選：輸入特定歌單名稱查看內含歌曲，留空則查看有哪些歌單")
async def playlist_view(interaction: discord.Interaction, playlist_name: str = None):
    embed = discord.Embed(color=discord.Color.gold())
    
    if playlist_name is None:
        embed.title = "📁 伺服器 YouTube 永久歌單庫"
        if not global_db["playlists"]:
            embed.description = "目前沒有任何永久歌單。趕快用 `/playlist_create` 建立一本吧！"
        else:
            text = "".join([f"• **{name}** (內含 `{len(songs)}` 首歌曲)\n" for name, songs in global_db["playlists"].items()])
            embed.description = text
    else:
        if playlist_name not in global_db["playlists"]:
            await interaction.response.send_message(f"❌ 找不到名為 `{playlist_name}` 的歌單。", ephemeral=True)
            return
        embed.title = f"📁 歌單內容：{playlist_name}"
        songs = global_db["playlists"][playlist_name]
        if not songs:
            embed.description = "*這本歌單目前裡面沒有歌，請用 `/playlist_add` 收藏歌曲。*"
        else:
            text = "".join([f"`{i}.` {s['title']}\n" for i, s in enumerate(songs[:20], start=1)])
            if len(songs) > 20:
                text += f"*...以及另外 {len(songs)-20} 首歌曲*"
            embed.description = text

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="playlist_play", description="▶️ 一鍵點播整本永久歌單中的所有歌曲")
@app_commands.describe(playlist_name="想要點播哪一本永久歌單")
async def playlist_play(interaction: discord.Interaction, playlist_name: str):
    if playlist_name not in global_db["playlists"]:
        await interaction.response.send_message(f"❌ 找不到名為 `{playlist_name}` 的歌單！", ephemeral=True)
        return
    songs = global_db["playlists"][playlist_name]
    if not songs:
        await interaction.response.send_message(f"❌ 歌單 `{playlist_name}` 內沒有任何歌曲，無法播放！", ephemeral=True)
        return
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 錯誤：你必須先進入一個語音頻道！", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    state = get_music_state(guild_id)
    state.interaction_channel = interaction.channel

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    if not voice_client:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    was_playing = voice_client.is_playing() or voice_client.is_paused()
    for s in songs:
        state.queue.append({
            'url': s['url'], 'title': s['title'], 'user': f"{interaction.user.mention} (來自歌單: {playlist_name})"
        })

    if not was_playing:
        play_next(guild_id, voice_client)
        await interaction.followup.send(f"🔥 成功載入整本歌單！立即開始播放【`{playlist_name}`】（共 `{len(songs)}` 首歌曲）！", ephemeral=True)
    else:
        await interaction.followup.send(f"✅ 成功將歌單【`{playlist_name}`】中的 `{len(songs)}` 首歌曲全部排入隊列尾端！", ephemeral=True)


@bot.tree.command(name="playlist_delete", description="🗑️ 永久刪除整本指定的自訂歌單 (不可復原)")
@app_commands.describe(playlist_name="想要刪除哪一本歌單的名稱")
async def playlist_delete(interaction: discord.Interaction, playlist_name: str):
    if playlist_name not in global_db["playlists"]:
        await interaction.response.send_message(f"❌ 錯誤：找不到名為 `{playlist_name}` 的歌單。", ephemeral=True)
        return
    del global_db["playlists"][playlist_name]
    save_db_to_disk() 
    await interaction.response.send_message(f"🗑️ 已永久刪除歌單【`{playlist_name}`】及其內含的所有歌曲！", ephemeral=True)


@bot.tree.command(name="playlist_remove_song", description="❌ 移除特定歌單中的某一首歌曲")
@app_commands.describe(playlist_name="哪一本歌單", song_number="歌曲編號 (請先用 /playlist_view 查看編號)")
async def playlist_remove_song(interaction: discord.Interaction, playlist_name: str, song_number: int):
    if playlist_name not in global_db["playlists"]:
        await interaction.response.send_message(f"❌ 錯誤：找不到名為 `{playlist_name}` 的歌單。", ephemeral=True)
        return
    songs = global_db["playlists"][playlist_name]
    if song_number < 1 or song_number > len(songs):
        await interaction.response.send_message(f"❌ 錯誤：無效的歌曲編號！該歌單目前只有 `{len(songs)}` 首歌曲。", ephemeral=True)
        return
    removed_song = songs.pop(song_number - 1)
    save_db_to_disk() 
    await interaction.response.send_message(f"❌ 已從【`{playlist_name}`】中移除第 `{song_number}` 首歌曲：**{removed_song['title']}**", ephemeral=True)


# ==================== 🎵 標準音樂管理指令區 ====================

@bot.tree.command(name="play", description="🎵 點單首歌曲 (支援 網址或關鍵字)")
@app_commands.describe(search="輸入網址、Spotify連結或歌名")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 錯誤：你必須先進入一個語音頻道！", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    state = get_music_state(guild_id)
    state.interaction_channel = interaction.channel
    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    if not voice_client:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    if "://spotify.com" in search:
        try:
            search = (await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(search.split('?')[0], download=False, process=False))).get('title', '').replace('| Spotify', '').strip()
        except Exception:
            pass

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        
        if 'entries' in data and len(data['entries']) > 0:
            track_data = data['entries'][0]
        else:
            track_data = data
            
        url, title = track_data['url'], track_data['title']
    except Exception as e:
        await interaction.followup.send(f"❌ 搜尋歌曲失敗: {e}", ephemeral=True)
        return

    new_song = {'url': url, 'title': title, 'user': interaction.user.mention}
    if voice_client.is_playing() or voice_client.is_paused():
        state.queue.append(new_song)
        await interaction.followup.send(f"✅ 已加入排隊隊列：**{title}**", ephemeral=True)
    else:
        state.queue.append(new_song)
        play_next(guild_id, voice_client)
        await interaction.followup.send(f"🎵 立即為您播放：**{title}**", ephemeral=True)


@bot.tree.command(name="queue", description="📋 查看當前的臨時排隊清單 (僅限您觀看)")
async def queue(interaction: discord.Interaction):
    state = get_music_state(interaction.guild.id)
    voice_client = interaction.guild.voice_client
    embed = discord.Embed(title="📋 當前臨時排隊清單", color=discord.Color.blue())
    status = f"**{state.current_title}**" if voice_client and (voice_client.is_playing() or voice_client.is_paused()) else "*目前沒有播放任何音樂*"
    embed.add_field(name="▶️ 正在播放", value=status, inline=False)
    q_text = "".join([f"`{i}.` **{s['title']}** (由 {s['user']} 點播)\n" for i, s in enumerate(state.queue[:10], start=1)]) if state.queue else "*目前臨時隊列空空如也*"
    embed.add_field(name="⏳ 接下來排隊歌曲", value=q_text, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pause", description="⏸️ 暫停播放音樂 (僅限您觀看通知)")
async def pause(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.response.send_message("⏸️ 成功暫停音樂！", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 目前音樂沒有在播放，無法暫停。", ephemeral=True)


@bot.tree.command(name="resume", description="▶️ 恢復播放音樂 (僅限您觀看通知)")
async def resume(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
        interaction.guild.voice_client.resume()
        await interaction.response.send_message("▶️ 音樂已恢復播放！", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 音樂並未處於暫停狀態，無法恢復。", ephemeral=True)


@bot.tree.command(name="volume", description="🎚️ 調整音量大小 (1-200，僅限您觀看設定)")
@app_commands.describe(volume_percent="音量百分比 (1 到 200)")
async def volume(interaction: discord.Interaction, volume_percent: int):
    vc = interaction.guild.voice_client
    if not vc or volume_percent < 1 or volume_percent > 200:
        await interaction.response.send_message("❌ 調整失敗，請輸入 1 至 200 的正確數字。", ephemeral=True)
        return
    state = get_music_state(interaction.guild.id)
    state.volume = volume_percent / 100.0
    if vc.source:
        vc.source.volume = state.volume
    save_db_to_disk() 
    await interaction.response.send_message(f"🎚️ 音量已調整並永久記憶為：`{volume_percent}%`", ephemeral=True)


@bot.tree.command(name="skip", description="⏭️ 跳過當前歌曲")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏭️ 已跳過當前歌曲！", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 目前沒有播歌，無法跳過。", ephemeral=True)


@bot.tree.command(name="stop", description="🛑 強制清空臨時隊列並讓機器人退出頻道")
async def stop(interaction: discord.Interaction):
    state = get_music_state(interaction.guild.id)
    state.queue.clear()
    state.current_title = "目前沒有播放任何歌曲"
    if interaction.guild.voice_client and interaction.guild.voice_client.is_connected():
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 已清空臨時隊列並退出頻道！*(您的永久保存歌單依然安好)*", ephemeral=True)


# ==================== 🧹 管理員清理指令區 ====================

@bot.tree.command(name="clear", description="🧹 批量刪除訊息 (限管理員權限，結果僅限您觀看)")
@app_commands.describe(amount="刪除數量 (1-100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int = 5):
    if amount < 1 or amount > 100: 
        await interaction.response.send_message("❌ 請輸入 1 至 100 之間的數字！", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 成功清理 `{len(deleted)}` 則訊息！", ephemeral=True)

@clear.error
async def clear_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ 您沒有「管理訊息」的權限，無法使用此指令！", ephemeral=True)


# ==================== 📊 狀態資訊顯示區 ====================

@bot.tree.command(name="nowplaying", description="顯示目前正在播放的音樂資訊")
async def now_playing(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ 目前語音頻道沒有在播放音樂喔！", ephemeral=True)
        return
        
    state = get_music_state(guild_id)

    embed = discord.Embed(
        title="🎵 正在播放 Now Playing",
        description=f"**{state.current_title}**",
        color=discord.Color.from_rgb(255, 45, 85)
    )
    
    embed.add_field(name="🎚️ 當前音量", value=f"```\n{int(state.volume * 100)}%\n```", inline=True)
    embed.add_field(name="⏳ 待播放歌曲數", value=f"```\n{len(state.queue)} 首\n```", inline=True)
    
    embed.set_footer(text=f"伺服器：{interaction.guild.name} • 狀態：播放中", icon_url=interaction.user.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed)

# ==================== 啟動程式 ====================
if __name__ == "__main__":
    keep_alive()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        raise ValueError("找不到 DISCORD_TOKEN 環境變數！請在 Render 後台設定。")
    bot.run(TOKEN)
