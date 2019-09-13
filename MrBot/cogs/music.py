from discord.ext import commands
from youtube_dl import YoutubeDL
from .utils.queue import Queue
from .utils import formatting
import itertools
import andesite
import discord
import random
import config
import os

import io
import typing
import speech_recognition as sr
import wave
from discord.opus import Decoder
import asyncio

ytdlopts = {
    'format': 'bestaudio',
    'outtmpl': 'files/voice/%(id)s.%(ext)s',
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'postprocessors':
        [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }
        ],
}
ytdl = YoutubeDL(ytdlopts)


class Track(andesite.Track):
    """
    Subclass of the track class to allow for storing the requester of the track and the channel it was added in.
    """

    def __init__(self, id_, info, *, ctx=None):
        super(Track, self).__init__(id_, info)
        self.channel = ctx.channel
        self.requester = ctx.author


class Player(andesite.Player):
    """
    Subclass of the player class that will allow for a per guild player loop and a "music controller"
    """

    def __init__(self, bot, guild_id: int, node):
        super(Player, self).__init__(bot, guild_id, node)
        self.bot.loop.create_task(self.player_loop())
        self.queue = Queue()
        self.queue_loop = False
        self.paused = False
        self.current = None
        self.volume = 50

        # Filters
        self.filter_count = 0
        self.nightcore = False

    async def stop(self):
        # Overriding stop because it clear current track.
        await self.node._websocket._send(op="stop", guildId=str(self.guild_id))

    async def player_loop(self):
        await self.bot.wait_until_ready()
        await self.set_volume(self.volume)
        await self.set_pause(self.paused)
        await self.set_karaoke()
        await self.set_timescale()
        await self.set_tremolo()
        await self.set_vibrato()
        while True:
            track = await self.queue.get_pos(0)
            if not track:
                continue
            self.current = track
            await self.play(track)
            await self.invoke_controller()
            await self.bot.wait_for("andesite_track_end", check=lambda p: p.player.guild_id == self.guild_id)
            if self.queue_loop is True:
                await self.queue.put(self.current)
            self.current = None

    async def invoke_controller(self):
        embed = discord.Embed(
            title="Music controller:",
            colour=0x57FFF5
        )

        # Set the embed thumbnail to the tracks thumbnail.
        embed.set_thumbnail(url=f"https://img.youtube.com/vi/{self.current.yt_id}/hqdefault.jpg")

        # Add a link for the name and url of the current track.
        embed.add_field(name=f"Now playing:", value=f"**[{self.current.title}]({self.current.uri})**", inline=False)

        # If the current track is a stream.
        if self.current.is_stream:
            embed.add_field(name="Time:", value="`Live stream`")

        # If its not a stream do add the length and position.
        else:
            embed.add_field(name="Time:", value=f"`{formatting.get_time(round(self.position) / 1000)}` / "
                                                f"`{formatting.get_time(self.current.length / 1000)}`")

        # Add various other bits of information to do with the track and queue.
        embed.add_field(name="Volume:", value=f"`{self.volume}%`")
        embed.add_field(name="Queue Length:", value=f"`{str(self.queue.qsize())}`")
        embed.add_field(name="Queue looped:", value=f"`{self.queue_loop}`")
        embed.add_field(name="Requester:", value=self.current.requester.mention)

        # Send the embed in the tracks channel.
        await self.current.channel.send(embed=embed)


class Music(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.initiate_nodes())
        self.listen = False

    async def initiate_nodes(self):

        nodes = {"Node_1": {"ip": config.IP_1,
                            "port": config.PORT_1,
                            "rest_uri": config.ADRESS_1,
                            "password": config.PASSWORD_1,
                            "identifier": config.IDENTIFIER_1,
                            }
                 }

        for n in nodes.values():
            try:
                await self.bot.andesite.start_node(
                    n["ip"],
                    n["port"],
                    rest_uri=n["rest_uri"],
                    password=n["password"],
                    identifier=n["identifier"]
                )
            except andesite.InvalidCredentials:
                print(f"\n[ANDESITE] Invalid credentials for node {n['identifier']}.")
            except ConnectionRefusedError:
                print(f"Failed to connect to node {n['identifier']}")
            print(f"\n[ANDESITE] Node {n['identifier']} connected.")

    @commands.command(name="join", aliases=["connect"])
    async def join(self, ctx):
        """
        Join or move to the users voice channel.
        """

        # If the user is not in a voice channel then tell them that have to be in one.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")
        channel = ctx.author.voice.channel

        # If the player is not already connected.
        if not ctx.player.is_connected or not ctx.guild.me.voice.channel:
            # Join the channel.
            await ctx.player.connect(channel.id)
            return await ctx.send(f"Joined the voice channel `{channel}`")

        # If the player is connected but the user is in another voice channel then move to that channel.
        if ctx.guild.me.voice.channel.id != channel.id:
            await ctx.player.connect(channel.id)
            return await ctx.send(f"Moved to the voice channel `{channel}`")

        # The bot must already be in this voice channel.
        return await ctx.send("I am already in this voice channel.")

    @commands.command(name="leave", aliases=["disconnect", "stop"])
    async def leave(self, ctx):
        """
        Leave the current voice channel.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # Disconnect and destory the player.
        ctx.player.queue.queue.clear()
        await ctx.player.disconnect()
        await ctx.player.destroy()
        return await ctx.send(f"Left the voice channel `{ctx.guild.me.voice.channel}`.")

    @commands.command(name="play")
    async def play(self, ctx, *, search: str):
        """
        Play a track using a link or search query.

        `search`: will default to a Youtube search however it also accepts links from SoundCloud and Twitch.
        """

        # If the user it not in a voice channel then tell them that have to be in one.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")
        channel = ctx.author.voice.channel

        # If the player is not already connected.
        if not ctx.player.is_connected:
            # Join the voice channel.
            await ctx.player.connect(channel.id)

        # Trigger typing.
        await ctx.trigger_typing()

        # Get a list of all the tracks for the users search term.
        tracks = await ctx.player.node.get_tracks(f"{search}")
        # If there were no tracks.
        if not tracks:
            return await ctx.send(f"No results were found for the search term `{search}`.")

        # If "tracks" was a playlist
        if isinstance(tracks, andesite.Playlist):
            # Loop through all the tracks in the playlist and add them to the queue
            for track in tracks.tracks:
                await ctx.player.queue.put(Track(track.id, track.data, ctx=ctx))
            return await ctx.send(f"Added the playlist **{tracks.name}** to the queue with a total of **{len(tracks.tracks)}** entries.")

        # Get the first entry in the list and add it to the queue.
        track = tracks[0]
        await ctx.player.queue.put(Track(track.id, track.data, ctx=ctx))
        return await ctx.send(f"Added the track **{track.title}** to the queue.")

    @commands.command(name="pause")
    async def pause(self, ctx):
        """
        Pause the current track.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If nothing is currently playing.
        if not ctx.player.current:
            return await ctx.send("No tracks currently playing.")

        # If the player is already paused.
        if ctx.player.paused is True:
            return await ctx.send("The current track is already paused.")

        await ctx.player.set_pause(True)
        return await ctx.send(f"Paused the current track.")

    @commands.command(name="resume")
    async def resume(self, ctx):
        """
        Resume the current track.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If nothing is current playing.
        if not ctx.player.current:
            return await ctx.send("No tracks currently playing.")

        # If the track is not paused
        if ctx.player.paused is False:
            return await ctx.send("The current track is not paused.")

        await ctx.player.set_pause(False)
        return await ctx.send(f"Resumed playback of the current track.")

    @commands.command(name="skip")
    async def skip(self, ctx, amount: int = 0):
        """
        Skip to the next track in the queue.

        This will auto skip if you are the requester of the current track, otherwise a vote will start to skip the track.

        `amount`: An optional number for skipping multiple tracks at once.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("There are no tracks in the queue to skip too.")

        # If the user is not the current tracks requester.
        if ctx.player.current.requester.id != ctx.author.id:
            # todo vote skipping.
            return await ctx.send("Vote skipping coming soon.")

        # If the user has not specified an amount of tracks to skip.
        if not amount:
            await ctx.player.stop()
            return await ctx.send(f"The current tracks requester has skipped the current track.")

        # If the amount of tracks to skip is smaller then 1 or larger then the amount of tracks in the queue.
        if amount <= 0 or amount > ctx.player.queue.qsize():
            return await ctx.send(f"That is not a valid amount of tracks to skip. Please choose a value between `1` and `{ctx.player.queue.qsize()}`")

        # Loop through the next "amount" of tracks in the queue
        for track in ctx.player.queue.queue[:amount - 1]:
            # If the user is not the requester of the tracks then return and dont skip.
            if not ctx.author.id == track.requester.id:
                return await ctx.send(f"You are not the requester of all `{amount}` of the next tracks in the queue.")
            # Else, skip remove the track from the queue.
            await ctx.player.queue.get_pos(0)

        # Now skip the current track and return.
        await ctx.player.stop()
        return await ctx.send(f"The current tracks requester has skipped `{amount}` tracks.")

    @commands.command(name="now_playing", aliases=["np"])
    async def now_playing(self, ctx):
        """
        Display information about the current song/queue status.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If nothing is current playing.
        if not ctx.player.current:
            return await ctx.send("No tracks currently playing.")

        embed = discord.Embed(
            title="Music controller:",
            colour=0x57FFF5
        )

        # Set the embed thumbnail to the tracks thumbnail.
        embed.set_thumbnail(url=f"https://img.youtube.com/vi/{ctx.player.current.yt_id}/hqdefault.jpg")

        # Add a link for the name and url of the current track.
        embed.add_field(name=f"Now playing:", value=f"**[{ctx.player.current.title}]({ctx.player.current.uri})**", inline=False)

        # If the current track is a stream.
        if ctx.player.current.is_stream:
            embed.add_field(name="Time:", value="`Live stream`")
        # If its not a stream do add the length and position.
        else:
            embed.add_field(name="Time:", value=f"`{formatting.get_time(round(ctx.player.position) / 1000)}` / "
                                                f"`{formatting.get_time(ctx.player.current.length / 1000)}`")

        # Add various other bits of information to do with the track and queue.
        embed.add_field(name="Volume:", value=f"`{ctx.player.volume}%`")
        embed.add_field(name="Queue Length:", value=f"`{str(ctx.player.queue.qsize())}`")
        embed.add_field(name="Queue looped:", value=f"`{ctx.player.queue_loop}`")
        embed.add_field(name="Requester:", value=ctx.player.current.requester.mention)

        # Send the embed
        await ctx.send(embed=embed)

    @commands.command(name="download")
    async def download(self, ctx):
        """
        Downloads an mp3 file of the current track.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If nothing is current playing.
        if not ctx.player.current:
            return await ctx.send("No tracks currently playing.")

        # Start process
        message = await ctx.send("Downloading track... <a:downloading:616426925350715393>")
        await ctx.trigger_typing()

        # Download the track in an executor, cuz this is blocking.
        track_title, track_id = await self.bot.loop.run_in_executor(None, self.do_download, ctx)

        if ctx.guild.premium_tier == 1 or ctx.guild.premium_tier == 0:
            size = os.path.getsize(f"files/voice/{track_id}.mp3")
            if size >= 8388608:
                os.remove(f"files/voice/{track_id}.mp3")
                return await ctx.send("This track is too big to upload to discord.")
        if ctx.guild.premium_tier == 2:
            size = os.path.getsize(f"files/voice/{track_id}.mp3")
            if size >= 52428800:
                os.remove(f"files/voice/{track_id}.mp3")
                return await ctx.send("This track is too big to upload to discord.")
        if ctx.guild.premium_tier == 3:
            size = os.path.getsize(f"files/voice/{track_id}.mp3")
            if size >= 104857600:
                os.remove(f"files/voice/{track_id}.mp3")
                return await ctx.send("This track is too big to upload to discord.")

        await message.edit(content="Uploading track... <a:downloading:616426925350715393>")

        # Upload the file
        await ctx.send(content="Here is your download.", file=discord.File(filename=f"{track_title}.mp3", fp=f"files/voice/{track_id}.mp3"))

        # Delete the file and message
        os.remove(f"files/voice/{track_id}.mp3")
        return await message.delete()

    def do_download(self, ctx):
        data = ytdl.extract_info(f"{ctx.player.current.uri}", download=False)
        ytdl.download([f"{ctx.player.current.uri}"])
        return data["title"], data["id"]

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx, volume: int = None):
        """
        Change the volume of the player.

        `volume`: The percentage to change the volume to, can be between 0 and 100.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the user doesnt input a volume to change too.
        if not volume:
            return await ctx.send(f"The current volume is `{ctx.player.volume}%`.")

        # Make sure the value is between 0 and 100.
        if volume < 0 or volume > 100:
            return await ctx.send(f"Please enter a value between `1` and and `100`.")

        ctx.player.volume = volume
        await ctx.player.set_volume(volume)
        return await ctx.send(f"Changed the players volume to `{volume}%`.")

    @commands.command(name="seek")
    async def seek(self, ctx, seconds: int = None):
        """
        Change the postion of the player.

        `position`: The position of the track to skip to in seconds.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If nothing is current playing.
        if not ctx.player.current:
            return await ctx.send("No tracks currently playing.")

        # If the current track is not seekable.
        if not ctx.player.current.is_seekable:
            return await ctx.send("This track is not seekable.")

        if not seconds:
            return await ctx.send(f"The current position is {formatting.get_time(ctx.player.position / 1000)}")

        # Check if the amount of time is between 0 and the length of the track.
        milliseconds = seconds * 1000
        if milliseconds <= 0 or milliseconds > ctx.player.current.length:
            return await ctx.send(f"Please enter a value between `1` and `{round(ctx.player.current.length / 1000)}`.")

        # Seek to the position
        await ctx.player.seek(milliseconds)
        return await ctx.send(f"Changed the players position to `{formatting.get_time(milliseconds / 1000)}`.")

    @commands.group(name="filter", aliases=["filters"], invoke_without_command=True)
    async def filter(self, ctx):

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If there are no filters.
        if ctx.player.filter_count == 0:
            return await ctx.send("No filters curently enabled.")

        # Send a list of current filters.
        message = f">>> **The current filters are:**\n"
        if ctx.player.nightcore is True:
            message += "   - Nightcore"
        return await ctx.send(message)

    @filter.command(name="nightcore")
    async def nightcore(self, ctx):

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # Basic toggling, it the filter is already on, turn it off.
        if ctx.player.nightcore is True:
            await ctx.player.set_timescale()
            ctx.player.filter_count -= 1
            ctx.player.nightcore = False
            return await ctx.send(f"Removed the nightcore filter.")
        await ctx.player.set_timescale(speed=1.1, pitch=1.1, rate=1)
        ctx.player.filter_count += 1
        ctx.player.nightcore = True
        return await ctx.send(f"Added the nightcore filter.")

    @commands.command(name="queue")
    async def queue(self, ctx):
        """
        Display the current queue.
        """

        # If the player is not connected then do nothing.
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("The queue is empty.")

        # Get the first 10 entries in the queue.
        upcoming = list(itertools.islice(ctx.player.queue.queue, 0, 10))

        # Define a message with information about the current song.
        message = f"__**Current track:**__\n[{ctx.player.current.title}]({ctx.player.current.uri}) | " \
                  f"`{formatting.get_time(round(ctx.player.current.length) / 1000)}` | " \
                  f"`Requested by:` {ctx.player.current.requester.mention}\n\n" \
                  f"__**Up next:**__: `{len(upcoming)}` out of `{ctx.player.queue.qsize()}` entries in the queue.\n"

        # Make a counter and loop through upcoming songs, adding info about them to the message.
        counter = 1
        for track in upcoming:
            message += f"**{counter}.** [{str(track.title)}]({track.uri}) | " \
                       f"`{formatting.get_time(round(track.length) / 1000)}` | " \
                       f"`Requested by:` {track.requester.mention}\n\n"
            counter += 1

        # Define a time and loop through all songs in the queue to get the total time.
        time = 0
        for track in ctx.player.queue.queue:
            time += track.length

        # Add extra info to the message.
        message += f"There are `{ctx.player.queue.qsize()}` tracks in the queue with a total time of `{formatting.get_time(round(time) / 1000)}`"

        # Create and send an embed.
        embed = discord.Embed(
            colour=0x57FFF5,
            timestamp=ctx.message.created_at,
            description=message
        )
        return await ctx.send(embed=embed)

    @commands.command(name="shuffle")
    async def shuffle(self, ctx):
        """
        Shuffle the queue.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("The queue is empty.")

        # Shuffle the queue.
        random.shuffle(ctx.player.queue.queue)
        return await ctx.send(f"The queue has been shuffled.")

    @commands.command(name="clear")
    async def clear(self, ctx):
        """
        Clear the queue.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("The queue is empty.")

        # Clear the queue.
        ctx.player.queue.queue.clear()
        return await ctx.send(f"Cleared the queue.")

    @commands.command(name="reverse")
    async def reverse(self, ctx):
        """
        Remove an entry from the queue.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("The queue is empty.")

        # Reverse the queue.
        ctx.player.queue.queue.reverse()
        return await ctx.send(f"Reversed the queue.")

    @commands.command(name="loop")
    async def loop(self, ctx):
        """
        Loop the queue.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If queue_loop is already True then set it to false, basic toggling.
        if ctx.player.queue_loop is True:
            ctx.player.queue_loop = False
            return await ctx.send(f"The queue will stop looping.")
        # Else set it to true.
        ctx.player.queue_loop = True
        return await ctx.send(f"The queue will now loop.")

    @commands.command(name="remove")
    async def remove(self, ctx, entry: int = 0):
        """
        Remove an entry from the queue.

        `entry`: The number of the entry you want to remove.
        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("The queue is empty.")

        # Check if the entry is between 0 and queue size.
        if entry <= 0 or entry > ctx.player.queue.qsize():
            return await ctx.send(f"That was not a valid track entry number.")

        # Remove the entry from the queue.
        item = await ctx.player.queue.get_pos(entry - 1)
        return await ctx.send(f"Removed `{item}` from the queue.")

    @commands.command(name="move")
    async def move(self, ctx, entry_1: int = 0, entry_2: int = 0):
        """
        Move an entry from one position to another in the queue.

        When moving entries use the number shown in the `queue` command. For example `mb move 1 15` will move the track in 1st position to 15th.

        `entry_1`: The number of the entry you want to move from.
        `entry_2`: The number of the entry you want to move too.


        """

        # If the player is not connected then do nothing
        if not ctx.player.is_connected:
            return await ctx.send(f"MrBot is not connected to any voice channels.")

        # If the user is not a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f"You must be in a voice channel to use this command.")

        # If the user is not in the same voice channel as the bot.
        if ctx.player.channel_id != ctx.author.voice.channel.id:
            return await ctx.send(f"You must be in the same voice channel as me to use this command.")

        # If the queue is empty.
        if ctx.player.queue.empty():
            return await ctx.send("The queue is empty.")

        # Check if the entry_1 is between 0 and queue size.
        if entry_1 <= 0 or entry_1 > ctx.player.queue.qsize():
            return await ctx.send(f"That was not a valid track to move from.")

        # Check if the entry is between 0 and queue size.
        if entry_2 <= 0 or entry_2 > ctx.player.queue.qsize():
            return await ctx.send(f"That was not a valid track to move too.")

        # get the track we want to move.
        item = await ctx.player.queue.get_pos(entry_1 - 1)

        # Search for it again.
        tracks = await ctx.player.node.get_tracks(f"{item}")
        track = tracks[0]

        # Move it the chose position.
        await ctx.player.queue.put_pos(Track(track.id, track.data, ctx=ctx), entry_2 - 1)
        return await ctx.send(f"Moved `{item}` from position `{entry_1}` to position `{entry_2}`.")

    @commands.is_owner()
    @commands.command(name='stt')
    async def stt(self, ctx, user: typing.Optional[discord.Member] = None):
        """
        Execute commands based on what you say.

        To use this first join a voice chat, use this command, and say `alexa`. When the message saying you have been recognised is sent, you will be able to say a command which will then be executes by the bot.
        """

        # Check if the user in a voice channel.
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send(f'You must be in a voice channel to use this command.')
        channel = ctx.author.voice.channel

        # Try to join channel, ignore errors.
        try:
            await channel.connect()
        except discord.ClientException:
            pass

        # If the bot is already listening.
        if self.listen is True:
            return await ctx.send('I am already listening.')

        # If the user doesn't pick someone to record, record them.
        if user is None:
            user = ctx.author

        # Tell the user that we are listening, then start.
        await ctx.send(f'I am listening to `{user}`. You can say `Alexa` to invoke commands.')
        self.listen = True
        await self.listen_to_audio(ctx, user)

    async def listen_to_audio(self, ctx, user):

        # Define the stream and sink and start listening.
        vc = ctx.voice_client
        found = False
        while self.listen is True:
            try:
                # While we have not found our keyword.
                while found is False:
                    # Create a new stream and start listening.
                    stream = io.BytesIO()
                    sink = MySink(stream)
                    vc.listen(discord.UserFilter(sink, user))
                    # Sleep for 3 seconds, this is so we can loop every 3 secs.
                    await asyncio.sleep(3)
                    # Stop listening.
                    vc.stop_listening()
                    # Speech recognition is blocking so try to check what was said in an executor.
                    msg = await self.bot.loop.run_in_executor(None, self.translate, stream)
                    # If the keyword was said.
                    if 'Alexa' in msg or "alexa" in msg:
                        await ctx.send('I have detected the keyword `Alexa`, you can say a command now.')
                        # Stop listening for our keyword.
                        found = True
                # Create a new stream and start listening for our command.
                stream = io.BytesIO()
                sink = MySink(stream)
                vc.listen(discord.UserFilter(sink, user))
                # Sleep for 7 or so seconds, this is so we the user has enough time to say their command.
                await asyncio.sleep(7)
                # Stop listening.
                vc.stop_listening()
                # Speech recognition is blocking so try to check what was said in an executor.
                msg = await self.bot.loop.run_in_executor(None, self.translate, stream)
                # If the message we get back is a specific code, give an error.
                if msg == '81431':
                    await ctx.send("I was unable to understand what you said. This could be because nothing was said, or because there is too much background noise.")
                    continue
                if msg == '75482':
                    await ctx.send("Error: I could not get the results from the service.")
                    continue
                # Otherwise add the bots prefix to the message
                msg = f'{ctx.prefix}{msg.lower()}'
                await ctx.send(f'Trying to invoke the command `{msg}`.')
                # Copy the message so that we can execute it as the user we are listening too.
                fake_msg = copy.copy(ctx.message)
                fake_msg.content = msg
                fake_msg.author = user
                # Process the command.
                await self.bot.process_commands(fake_msg)
                found = False
            except Exception as e:
                print(e)

    def translate(self, stream: io.BytesIO):
        # Listen to the audio from the file, adjusting for the ambient noise.
        stream.seek(0)
        with sr.WavFile(stream) as source:
            sr.Recognizer().adjust_for_ambient_noise(source, duration=0.25)
            audio = sr.Recognizer().record(source)
        try:
            # Try to translate it into text.
            msg = sr.Recognizer().recognize_google(audio)
        except sr.UnknownValueError:
            # Return a specific code if it was not understandable
            msg = '81431'
        except sr.RequestError as e:
            msg = '75482'
            print(f"Could not request results from service. {e}")
        return msg


class MySink(discord.AudioSink):

    def __init__(self, destination):

        self.destination = destination
        self.file = wave.open(destination, 'wb')
        self.file.setnchannels(Decoder.CHANNELS)
        self.file.setsampwidth(Decoder.SAMPLE_SIZE//Decoder.CHANNELS)
        self.file.setframerate(Decoder.SAMPLING_RATE)

    def write(self, data):
        self.file.writeframes(data.data)

    def cleanup(self):
        self.file.close()

def setup(bot):
    bot.add_cog(Music(bot))

