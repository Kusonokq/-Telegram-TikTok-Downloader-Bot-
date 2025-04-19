import telebot
import yt_dlp
import requests
from pathlib import Path
import os
from dotenv import load_dotenv
import logging
import re
from bs4 import BeautifulSoup
from telebot.types import InputMediaPhoto

# Load environment variables from info.env
load_dotenv('info.env')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Initialize the bot
bot = telebot.TeleBot(BOT_TOKEN)

# Set up logging
logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Create tempDownload directory if it doesn't exist
temp_dir = Path('tempDownload')
temp_dir.mkdir(exist_ok=True)

# Function to generate unique file names
def get_next_file_name(prefix, extension):
    existing_files = list(temp_dir.glob(f'{prefix}_*.{extension}'))
    if not existing_files:
        return f'{prefix}_001.{extension}'
    max_number = max(int(f.stem.split('_')[1]) for f in existing_files)
    next_number = max_number + 1
    return f'{prefix}_{next_number:03d}.{extension}'

# Function to check if the text is a TikTok link
def is_tiktok_link(text):
    patterns = [
        r'https?://(www\.)?tiktok\.com/@[\w\.-]+/video/\d+',  # Standard TikTok video URL
        r'https?://(www\.)?tiktok\.com/@[\w\.-]+/photo/\d+',  # TikTok photo URL
        r'https?://vt\.tiktok\.com/[A-Za-z0-9]+/'  # Short TikTok URL
    ]
    return any(re.match(pattern, text) for pattern in patterns)

# Function to resolve short TikTok URLs
def resolve_short_url(short_url):
    try:
        response = requests.head(short_url, allow_redirects=True, timeout=10)
        resolved_url = response.url
        logging.info(f"Resolved short URL {short_url} to {resolved_url}")
        return resolved_url
    except Exception as e:
        logging.error(f"Error resolving short URL {short_url}: {e}")
        return None

# Function to download TikTok content without watermark
def download_tiktok_content(url):
    try:
        # Resolve short URL if necessary
        if 'vt.tiktok.com' in url:
            url = resolve_short_url(url)
            if not url or 'tiktok.com' not in url:
                logging.error(f"Invalid resolved URL: {url}")
                return None, None

        # Determine content type
        content_type = 'photo' if '/photo/' in url else 'video'

        if content_type == 'video':
            # Use yt-dlp for videos: https://github.com/yt-dlp/yt-dlp
            ydl_opts = {
                'outtmpl': str(temp_dir / 'temp_%(id)s.%(ext)s'),
                'format': 'best',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'nowatermark': True,  # Attempt to download without watermark
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    logging.error(f"No video info found for URL: {url}")
                    return None, None

                file_path = next((str(f) for f in temp_dir.glob('temp_*.mp4')), None)
                if not file_path:
                    logging.error(f"No video file downloaded for URL: {url}")
                    return None, None

                extension = 'mp4'
                # Rename file according to requirements
                new_file_name = get_next_file_name(f'temp{content_type.capitalize()}', extension)
                new_file_path = temp_dir / new_file_name
                os.rename(file_path, new_file_path)
                logging.info(f"Downloaded file: {new_file_path} (type: {content_type})")
                return [str(new_file_path)], content_type
        else:
            # Use ssstik.io for photos
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            data = {'id': url, 'locale': 'en', 'tt': '0'}
            response = requests.post('https://ssstik.io/abc?url=dl', headers=headers, data=data, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            # Find all slide download links for photos
            slide_links = soup.find_all('a', string=re.compile('Download this slide'))
            if not slide_links:
                logging.error("No image URLs found for photo")
                return None, None

            file_paths = []
            for idx, slide_link in enumerate(slide_links):
                media_url = slide_link['href']
                if not media_url.startswith('http'):
                    media_url = 'https://ssstik.io' + media_url

                # Download each photo
                file_name = get_next_file_name('tempPhoto', 'png')
                file_path = temp_dir / file_name
                response = requests.get(media_url, stream=True, headers=headers, timeout=30)
                response.raise_for_status()
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                # Rename file according to requirements
                new_file_name = get_next_file_name('tempPhoto', 'png')
                new_file_path = temp_dir / new_file_name
                os.rename(file_path, new_file_path)
                file_paths.append(str(new_file_path))
                logging.info(f"Downloaded photo {idx + 1}: {new_file_path}")

            return file_paths, content_type
    except Exception as e:
        logging.error(f"Error downloading TikTok content: {e}")
        return None, None

# Function to process TikTok link
def process_tiktok_link(message):
    file_paths = None
    try:
        # Download content
        file_paths, content_type = download_tiktok_content(message.text)
        if not file_paths or not content_type:
            bot.reply_to(message, "Не удалось скачать контент. Проверьте ссылку или попробуйте позже.")
            return

        if content_type == 'video':
            # Process video (single file)
            for file_path in file_paths:
                # Check file size
                file_size = Path(file_path).stat().st_size
                if file_size == 0:
                    logging.error(f"Downloaded file is empty: {file_path}")
                    bot.reply_to(message, f"Ошибка: файл {file_path} пустой. Попробуйте другую ссылку.")
                    continue
                logging.info(f"File size: {file_size} bytes")

                # Send the video to the user
                with open(file_path, 'rb') as f:
                    bot.send_video(message.chat.id, f, supports_streaming=True)
                logging.info(f"File sent: {file_path}")
        else:
            # Process photos (multiple files in one message)
            media_group = []
            for file_path in file_paths:
                # Check file size
                file_size = Path(file_path).stat().st_size
                if file_size == 0:
                    logging.error(f"Downloaded file is empty: {file_path}")
                    bot.reply_to(message, f"Ошибка: файл {file_path} пустой. Попробуйте другую ссылку.")
                    continue
                logging.info(f"File size: {file_size} bytes")

                # Add photo to media group
                with open(file_path, 'rb') as f:
                    media_group.append(InputMediaPhoto(f.read()))
                logging.info(f"Added to media group: {file_path}")

            # Send all photos in one message
            if media_group:
                bot.send_media_group(message.chat.id, media_group)
                logging.info(f"Sent media group with {len(media_group)} photos")

    except Exception as e:
        logging.error(f"Error processing link {message.text}: {e}")
        bot.reply_to(message, f"Произошла ошибка: {str(e)}")
    finally:
        # Ensure files are deleted even if an error occurs
        if file_paths:
            for file_path in file_paths:
                if Path(file_path).exists():
                    try:
                        os.remove(file_path)
                        logging.info(f"File deleted: {file_path}")
                    except Exception as e:
                        logging.error(f"Error deleting file {file_path}: {e}")

# Message handler for text input
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if is_tiktok_link(message.text):
        logging.info(f"Received TikTok link from {message.chat.id}: {message.text}")
        bot.reply_to(message, "Обрабатываю вашу ссылку...")
        process_tiktok_link(message)
    else:
        bot.reply_to(message, "Пожалуйста, отправьте действительную ссылку на TikTok (например, https://vt.tiktok.com или https://www.tiktok.com/.")

# Start the bot
if __name__ == "__main__":
    logging.info("Bot started")
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        logging.error(f"Bot polling error: {e}")
        bot.stop_polling()