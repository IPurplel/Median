from typing import Optional, Callable
from backend.config import settings


FORMAT_EXT_MAP = {
    'mp3': 'mp3', 'flac': 'flac', 'aac': 'm4a',
    'mp4': 'mp4', 'mkv': 'mkv', 'webm': 'webm',
    'opus': 'webm',
}

AUDIO_FORMATS = {'mp3', 'flac', 'aac'}
VIDEO_FORMATS = {'mp4', 'mkv', 'webm'}
AUDIO_FORMATS_OPUS = {'mp3', 'flac', 'aac', 'opus'}


def get_ydl_opts(
    download_type: str,
    fmt: str,
    bitrate: str,
    output_template: str,
    progress_hook: Optional[Callable] = None
) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'outtmpl': output_template,
        'socket_timeout': 60,
        'retries': 3,
        'fragment_retries': 3,
        'concurrent_fragment_downloads': 4,
        'http_chunk_size': settings.DOWNLOAD_CHUNK_SIZE * 1024 * 1024,
    }

    if progress_hook:
        opts['progress_hooks'] = [progress_hook]

    bitrate_val = (bitrate or '').replace('kbps', '').strip() if bitrate else ''

    if download_type == 'audio':
        opts['format'] = 'bestaudio/best'
        opts['writethumbnail'] = True
        if fmt == 'flac':
            postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'flac'}]
        elif fmt == 'mp3':
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
            if bitrate_val:
                pp['preferredquality'] = bitrate_val
            postprocessors = [pp]
        elif fmt in ('aac', 'm4a'):
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'aac'}
            if bitrate_val:
                pp['preferredquality'] = bitrate_val
            postprocessors = [pp]
        elif fmt == 'opus':
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'opus', 'preferredquality': '0'}
            postprocessors = [pp]
        else:
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
            if bitrate_val:
                pp['preferredquality'] = bitrate_val
            postprocessors = [pp]
        postprocessors += [
            {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
            {'key': 'FFmpegMetadata', 'add_metadata': True},
            {'key': 'EmbedThumbnail'},
        ]
        opts['postprocessors'] = postprocessors
        # YouTube has no genre field — fall back to its first category (e.g. "Music").
        # SoundCloud/Bandcamp have a real genre field, so that takes precedence.
        # FFmpegMetadataPP only reads info['artist'] with no fallback, so for
        # YouTube videos (where artist is None) we must pre-populate it here.
        opts['parse_metadata'] = [
            '%(genre,categories.0)s:%(genre)s',
            '%(artist,uploader,channel,creator)s:%(artist)s',
        ]

    elif download_type == 'video':
        if fmt == 'webm':
            if bitrate_val:
                opts['format'] = f'bestvideo[ext=webm]+bestaudio[ext=webm][abr<={bitrate_val}]/bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'
            else:
                opts['format'] = 'bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'
        elif fmt == 'mkv':
            if bitrate_val:
                opts['format'] = f'bestvideo+bestaudio[abr<={bitrate_val}]/bestvideo+bestaudio/best'
            else:
                opts['format'] = 'bestvideo+bestaudio/best'
            opts['merge_output_format'] = 'mkv'
        else:
            if bitrate_val:
                opts['format'] = f'bestvideo[ext=mp4]+bestaudio[ext=mp4][abr<={bitrate_val}]/bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/best'
            else:
                opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/best'
            opts['merge_output_format'] = 'mp4'

    elif download_type == 'cover_audio':
        opts['format'] = 'bestaudio/best'
        pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
        if bitrate_val:
            pp['preferredquality'] = bitrate_val
        opts['postprocessors'] = [pp]
        opts['writethumbnail'] = True
        opts['postprocessors'].append({'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'})

    if download_type == 'video':
        opts['writethumbnail'] = False

    return opts
