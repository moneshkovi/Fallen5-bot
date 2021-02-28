import discord
from discord.ext import commands
from discord.ext.tasks import loop
import asyncio
import sqlite3
import time
from discord.ext.commands import has_permissions,RoleConverter, MemberConverter
from datetime import datetime, timezone, timedelta
import aiohttp
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import json
import youtube_dl
import requests
import spotipy
from spotipy import util
from spotipy.oauth2 import SpotifyClientCredentials
import itertools
import sys
import os
import traceback
from async_timeout import timeout
from functools import partial
from youtube_dl import YoutubeDL




client_id = '4c6b72e867f641b497b4e9b391456174' #Need to create developer profile
client_secret = 'a692fc0179034e95867af85216323043'
username = 'username' #Store username
scope = 'user-library-read playlist-modify-public playlist-read-private'
redirect_uri='http://127.0.0.1:5000'
client_credentials_manager = SpotifyClientCredentials(client_id=client_id,
client_secret=client_secret)#Create manager for ease
sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)


client = discord.Client()
client = commands.Bot(".")

rol = []
spam =[]



ytdlopts = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # ipv6 addresses cause issues sometimes
}

ffmpegopts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)


class VoiceConnectionError(commands.CommandError):
    """Custom Exception class for connection errors."""


class InvalidVoiceChannel(VoiceConnectionError):
    """Exception for cases of invalid Voice Channels."""


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')
        self.filename = ytdl.prepare_filename(data)


    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_playlist(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)


        # take items from a playlist
        songs=[]
        for data in data['entries']:





            if download:
                source = ytdl.prepare_filename(data)

                songs.append(cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author))
            else:
                songs.append({'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']})


        return songs

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]



        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author)


    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)


        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpegopts), data=data, requester=requester)


class MusicPlayer(commands.Cog):
    """A class which is assigned to each guild using the bot for Music.
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    When the bot disconnects from the Voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None  # Now playing message
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                # So we should regather to prevent stream expiration
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source
            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**Now Playing:** `{source.title}` requested by '
                                               f'`{source.requester}`')

            await self.next.wait()
            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            self.current = None
            if os.path.exists(source.filename):
                print(str(source.filename))       #remove "#" if download is TRUE
                os.remove(source.filename)
            try:
                # We are no longer playing this song...
                await self.np.delete()


            except discord.HTTPException:
                pass

    def destroy(self, guild):
        """Disconnect and cleanup the player."""
        return self.bot.loop.create_task(self._cog.cleanup(guild))


class Music(commands.Cog):
    """Music related commands."""
    __slots__ = ('bot', 'players')

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    async def __local_check(self, ctx):
        """A local check which applies to all commands in this cog."""
        if not ctx.guild:
            raise commands.NoPrivateMessage
        return True

    async def __error(self, ctx, error):
        """A local error handler for all errors arising from commands in this cog."""
        if isinstance(error, commands.NoPrivateMessage):
            try:
                return await ctx.send('This command can not be used in Private Messages.')
            except discord.HTTPException:
                pass
        elif isinstance(error, InvalidVoiceChannel):
            await ctx.send('Error connecting to Voice Channel. '
                           'Please make sure you are in a valid channel or provide me with one')

        print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    @commands.command(name='connect', aliases=['join'])
    async def connect_(self, ctx, *, channel: discord.VoiceChannel = None):
        """Connect to voice.
        Parameters
        ------------
        channel: discord.VoiceChannel [Optional]
            The channel to connect to. If a channel is not specified, an attempt to join the voice channel you are in
            will be made.
        This command also handles moving the bot to different channels.
        """
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            if not channel:
                try:
                    channel = ctx.author.voice.channel
                except AttributeError:
                    await ctx.send("No channel to join. Please either specify a valid channel or join one.", delete_after=20)
                    raise InvalidVoiceChannel('No channel to join. Please either specify a valid channel or join one.')

            vc = ctx.voice_client

            if vc:
                if vc.channel.id == channel.id:
                    return
                try:
                    await vc.move_to(channel)
                except asyncio.TimeoutError:
                    await ctx.send("f'Moving to channel: <{channel}> timed out.", delete_after=20)
                    raise VoiceConnectionError(f'Moving to channel: <{channel}> timed out.')
            else:
                try:
                    await channel.connect()
                except asyncio.TimeoutError:
                    await ctx.send("f'Connecting to channel: <{channel}> timed out." , delete_after=20)
                    raise VoiceConnectionError(f'Connecting to channel: <{channel}> timed out.')

            await ctx.send(f'Connected to: **{channel}**', delete_after=20)
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:


    @commands.command(name="playlist", help="Request songs frm spotify")
    async def playlist_(self, ctx, *, playlist):
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            list = []
            segment = playlist.rpartition('/')
            playlist_id = 'spotify:user:spotifycharts:playlist:' + segment[2]
            results = sp.playlist(playlist_id)
            x = json.dumps(results, indent=4)
            y = json.loads(x)
            for i in y['tracks']['items']:
                list.append(i['track']['name'])
            await ctx.trigger_typing()

            vc = ctx.voice_client

            if not vc:
                await ctx.invoke(self.connect_)

            player = self.get_player(ctx)

            for i in list:
                source = await YTDLSource.create_source(ctx, i, loop=self.bot.loop, download=False)
                await player.queue.put(source)
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:


    @commands.command(name='play', aliases=['single'])
    async def play_(self, ctx, *, search: str):
        """Request a song and add it to the queue.
        This command attempts to join a valid voice channel if the bot is not already in one.
        Uses YTDL to automatically search and retrieve a song.
        Parameters
        """
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            await ctx.trigger_typing()

            vc = ctx.voice_client

            if not vc:
                await ctx.invoke(self.connect_)

            player = self.get_player(ctx)

            # If download is False, source will be a dict which will be used later to regather the stream.
            # If download is True, source will be a discord.FFmpegPCMAudio with a VolumeTransformer.
            source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=True)

            await player.queue.put(source)
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='pause')
    async def pause_(self, ctx):
        """Pause the currently playing song."""
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_playing():
                return await ctx.send('I am not currently playing anything!', delete_after=20)
            elif vc.is_paused():
                return

            vc.pause()
            await ctx.send(f'**`{ctx.author}`**: Paused the song!')
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='resume')
    async def resume_(self, ctx):
        """Resume the currently paused song."""
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return await ctx.send('I am not currently playing anything!', delete_after=20)
            elif not vc.is_paused():
                return

            vc.resume()
            await ctx.send(f'**`{ctx.author}`**: Resumed the song!')
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='skip')
    async def skip_(self, ctx):
        """Skip the song."""
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return await ctx.send('I am not currently playing anything!', delete_after=20)

            if vc.is_paused():
                pass
            elif not vc.is_playing():
                return

            vc.stop()
            await ctx.send(f'**`{ctx.author}`**: Skipped the song!')
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='queue', aliases=['q'])
    async def queue_info(self, ctx):
        """Retrieve a basic queue of upcoming songs."""
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return await ctx.send('I am not currently connected to voice!', delete_after=20)

            player = self.get_player(ctx)
            if player.queue.empty():
                return await ctx.send('There are currently no more queued songs.')

            # Grab up to 5 entries from the queue...
            upcoming = list(itertools.islice(player.queue._queue, 0, 5))

            fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
            embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)

            await ctx.send(embed=embed)
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='now_playing', aliases=['np', 'current', 'currentsong', 'playing'])
    async def now_playing_(self, ctx):
        """Display information about the currently playing song."""
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return await ctx.send('I am not currently connected to voice!', delete_after=20)

            player = self.get_player(ctx)
            if not player.current:
                return await ctx.send('I am not currently playing anything!')

            try:
                # Remove our previous now_playing message.
                await player.np.delete()
            except discord.HTTPException:
                pass

            player.np = await ctx.send(f'**Now Playing:** `{vc.source.title}` '
                                       f'requested by `{vc.source.requester}`')
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='volume', aliases=['vol'])
    async def change_volume(self, ctx, *, vol: float):
        """Change the player volume.
        Parameters
        ------------
        volume: float or int [Required]
            The volume to set the player to in percentage. This must be between 1 and 100.
        """
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return await ctx.send('I am not currently connected to voice!', delete_after=20)

            if not 0 < vol < 101:
                return await ctx.send('Please enter a value between 1 and 100.')

            player = self.get_player(ctx)

            if vc.source:
                vc.source.volume = vol / 100

            player.volume = vol / 100
            await ctx.send(f'**`{ctx.author}`**: Set the volume to **{vol}%**')
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

    @commands.command(name='stop')
    async def stop_(self, ctx):
        """Stop the currently playing song and destroy the player.
        !Warning!
            This will destroy the player assigned to your guild, also deleting any queued songs and settings.
        """
        fallen5_bot_channel_id = 767355069883744266
        if ctx.channel.id == fallen5_bot_channel_id:
            vc = ctx.voice_client

            if not vc or not vc.is_connected():
                return await ctx.send('I am not currently playing anything!', delete_after=20)

            await self.cleanup(ctx.guild)
        else:
            await ctx.send("Your in the wrong channel,Please go to <#767355069883744266>")  #fallen5_bot_channel_id:

client.add_cog(Music(client))



@client.event
async def on_ready():
    print('Bot is online')



@client.event
async def on_message(message):

    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Over F5"))

    def checki(id):
        def inner_checki(message):
            return message.channel.id == id
        return inner_checki

    if message.guild is None:
        em = discord.Embed(title="Message Send")
        em.add_field(name="details", value=f"{message.author} your message has been sent to the Admins. They will respond ASAP.")
        await message.author.send(embed=em)
        guild = client.get_guild(761135487640535040) # Server_id_Fallen_five=guild_id
        for i in guild.channels:
            if i.name == message.author.name.lower():
                x = message.author.name.lower()
                channel = discord.utils.get(guild.channels, name=f"{x}")
                em2 = discord.Embed(title="Message Recieved")
                em2.add_field(name=f"{message.author}", value=f"{message.content}")
                await channel.send(embed=em2)
                time.sleep(1)
                bruh = await client.wait_for('message', check=checki(channel.id))
                if bruh:
                    em3 = discord.Embed(title="Message Recieved")
                    em3.add_field(name=f"{message.author}", value="You got a message" +"\n" + f"{bruh.content}")
                    await message.author.send(embed=em3)
                    return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
        }
        category = discord.utils.get(guild.categories, id=767363567144927242)  # DM_bot_Category_id_fallen_five
        channel = await guild.create_text_channel(f"{message.author.name}", overwrites=overwrites, category=category )

        em2 = discord.Embed(title="Message Send")
        em2.add_field(name=f"{message.author}", value=f"{message.content}")
        await channel.send(embed=em2)
        time.sleep(1)
        bruh = await client.wait_for('message', check=checki(channel.id))
        if bruh:
            em3 = discord.Embed(title="Message Recieved")
            em3.add_field(name=f"{message.author}", value="You got a message" +"\n" + f"{bruh.content}")
            await message.author.send(embed=em3)
            return
    else:
        if message.author == client.user:
                return

        text = message.content
        urls = []
        urls = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
        for i in message.author.roles:
            if i.name == "." or i.name == "Fallen5" or i.name == "BOTS":
                await client.process_commands(message)
                return

        if urls != []:
            if spam.count(str(message.author.name)) >= 6:
                await message.delete()
                await message.channel.send("YOU ARE BANNED")
                val = str(message.author.name)
                try:
                    while True:
                        spam.remove(val)
                except ValueError:
                    pass
                print(spam)
            elif spam.count(str(message.author.name)) >= 3:
                await message.delete()
                role = discord.utils.get(message.guild.roles, name="Muted")
                await message.author.add_roles(role)
                await message.channel.send("You are muted")
                await asyncio.sleep(50)
                await message.author.remove_roles(role)
                await message.channel.send("ok times up")
            else:
                await message.delete()
                await message.channel.send(f"No links allowed {message.author.mention}")
                spam.append(str(message.author.name))
                print(spam)
            urls = []
    await client.process_commands(message)




#about developer
@client.command(name="abt_devlpr", help="Know about the developer")
async def abt_devlpr(ctx):
    channel = ctx.message.channel
    await channel.send(' <@599884619139121152> ')
    await channel.send(' *Editor @wirally_codm* ')
    await channel.send(' https://instagram.com/wirally_codm?igshid=16z3zqw8k5f0i')




#purge command
@client.command(name="purge", help="Administrative Use")
@has_permissions(administrator=True)
async def purge(ctx, limit: int):
        await ctx.channel.purge(limit=limit)
        await ctx.message.delete()
@purge.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("**You don't have the permission to invoke this command!**")




#anounces as per roles tagged SYNTAX - .announce @role.
@client.command(name="announce",help="Administrative Use")
@has_permissions(administrator=True)
async def announce(ctx, role: discord.Role):
    def check(author):
        def inner_check(message):
            return message.author == author
        return inner_check
    await ctx.send("*Type your message*")
    x= await client.wait_for('message',check =check(ctx.author))
    if x:
      em = discord.Embed(title="Announcement")
      em.add_field(name=f"{ctx.author}",     value=f"{x.content}")
      for i in role.members:
          await i.send(embed=em)
    await ctx.send("*Annoucement Done !*")
@announce.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("**You don't have the permission to invoke this command!**")



#delete command to delete roles on command SYNTAX - .delete @role
@client.command(name='delete',help="Administrative Use")
@has_permissions(administrator=True)
async def delete(ctx, role:discord.Role):
    await role.delete()
    await ctx.channel.send("**ROLE DELTED**")
@delete.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("**You don't have the permission to invoke this command!**")




#whois comamnd to know anything SYNTAX - .whois @tag
@client.command(name="whois",help="Administrative Use ")
@has_permissions(administrator=True)
async def whois(ctx, member:discord.Member):
    list = []
    perm = []
    em = discord.Embed(title=member.name)
    em.set_image(url=member.avatar_url)
    em.add_field(name="Joined at", value=member.created_at.strftime("%b %d, %Y"), inline=False)
    for i in member.roles:
        if i.name != "@everyone":
            list.append(i.id)
    em.add_field(name="Roles", value='<@&'+'><@&'.join(str(v) for v in list)+">", inline=False)
    for i in member.roles:
        if i.name == ".":
            ack = " Owner "
            break
        elif i.name == "Fallen5":
            ack = "PLayer for Fallen5 eSports"
            break
        elif i.name == "BOTS":
            ack = "Server Bot"
            break
        else:
            ack = "Server Member"
    em.add_field(name="Acknowledgement", value=ack, inline=False)
    for i in member.guild_permissions:
        if i[1] == True:
            perm.append(i[0])
    em.add_field(name="permission", value=", ".join(str(v) for v in perm))
    await ctx.send(embed=em)
@whois.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("**You don't have the permission to invoke this command!**")




client.run("Token")
