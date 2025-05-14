import os
import re
import streamlit as st
from datetime import date, datetime
import yt_dlp
import pandas as pd
import random # Розкоментовано для gpt_comment_summary
import json # Додано для парсингу JSON від GPT
from googleapiclient.discovery import build
from openai import OpenAI

# nltk та SentimentIntensityAnalyzer не потрібні, якщо sentiment_analysis видалено
# sklearn та matplotlib не використовуються

# --- Ключі API (тепер зі Streamlit Secrets) ---
OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY")
YT_API_KEY = st.secrets.get("YOUTUBE_API_KEY")

# Ініціалізація клієнта OpenAI
if not OPENAI_API_KEY:
    st.error("Ключ OpenAI API (OPENAI_API_KEY) не знайдено у Streamlit Secrets. Будь ласка, додайте його.")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

# Перевірка ключа YouTube Data API
if not YT_API_KEY:
    st.warning(
        "Ключ YouTube Data API (YOUTUBE_API_KEY) не знайдено у Streamlit Secrets. Функції, що його потребують, можуть не працювати.")

# Налаштування сторінки (має бути першою командою Streamlit)
st.set_page_config(page_title="YouTube Analytics Agent", layout="wide")

st.title("YouTube Analytics Agent")
st.markdown(
    """
    Інтерактивний агент для аналізу YouTube-каналів.
    """
)

# --- Функції ---

def extract_channel_id(url_input):
    """Витягує ID каналу або ім'я користувача з URL."""
    match_user = re.search(r"(?:https?://)?(?:www\.)?youtube\.com/@([^/?]+)", url_input)
    if match_user:
        return match_user.group(1)
    match_channel = re.search(r"(?:https?://)?(?:www\.)?youtube\.com/channel/([^/?]+)", url_input)
    if match_channel:
        return match_channel.group(1)
    return None


def fetch_video_metadata(channel_id_or_user, start_date, end_date, limit=10, show_all=False):
    """
    Збирає метадані відео з каналу або від користувача за допомогою yt_dlp.
    Фільтрує за датою.
    Примітка: ця функція наразі не використовується в основному потоці UI.
    """
    if not channel_id_or_user:
        st.error("Не надано ID каналу або ім'я користувача.")
        return []

    # Формування URL для yt-dlp
    if channel_id_or_user.startswith("UC"):  # Це ID каналу
        # yt-dlp може обробляти URL каналу, але для повного списку завантажень може знадобитися playlists URL
        # Для простоти, використовуємо загальний URL каналу.
        # Для отримання плейлиста "uploads" зазвичай потрібен API, щоб отримати ID плейлиста (UU...).
        # yt-dlp спробує отримати відео зі сторінки /videos
        url = f"https://www.youtube.com/channel/{channel_id_or_user}/videos"
        st.info(f"Спроба збору відео для ID каналу: {channel_id_or_user}. Результат може бути неповним для всіх відео каналу без прямого ID плейлиста завантажень.")
    else:  # Це ім'я користувача (після @)
        url = f"https://www.youtube.com/@{channel_id_or_user}/videos"

    opts_flat = {
        'ignoreerrors': True,
        'skip_download': True,
        'extract_flat': 'discard_in_playlist',
        'dump_single_json': True,
        'playlistend': limit if not show_all else None,
        'quiet': True,
        'no_warnings': True,
    }

    video_ids = []
    try:
        with yt_dlp.YoutubeDL(opts_flat) as ydl:
            info = ydl.extract_info(url, download=False)
        
        if info and 'entries' in info and info['entries']:
            video_ids = [e['id'] for e in info['entries'] if e and e.get('id')]
        elif info and info.get('id') and not info.get('entries'): # Якщо URL вказує на одне відео
             st.warning(f"URL {url} схожий на URL одного відео, а не каналу/плейлиста. Ця функція очікує URL каналу.")
             # Можна або повернути помилку, або спробувати обробити це одне відео, 
             # але це виходить за рамки початкового призначення функції.
             # video_ids = [info['id']] # Якщо вирішимо обробляти
             return []


    except Exception as e:
        st.error(f"Помилка на етапі flat-парсингу yt_dlp для {url}: {e}")
        return []

    if not video_ids:
        st.warning(f"Не знайдено ID відео для {url} на першому етапі.")
        return []

    opts_det = {
        'ignoreerrors': True, 
        'skip_download': True, 
        'extract_flat': False,
        'quiet': True,
        'no_warnings': True,
    }
    videos = []

    video_ids_to_process = video_ids
    if not show_all and len(video_ids) > limit * 1.5: # Невеликий буфер, якщо playlistend не спрацював ідеально
        video_ids_to_process = video_ids[:int(limit * 1.5)]


    for vid_id in video_ids_to_process:
        if not show_all and len(videos) >= limit:
            break
        
        # Використовуємо стандартний URL для відео
        vurl = f"https://www.youtube.com/watch?v={vid_id}"
        try:
            with yt_dlp.YoutubeDL(opts_det) as ydl:
                vinfo = ydl.extract_info(vurl, download=False)

            if not vinfo:
                continue

            upload_date_str = vinfo.get('upload_date')
            publish_date = None
            if upload_date_str:
                try:
                    publish_date = datetime.strptime(upload_date_str, '%Y%m%d').date()
                except ValueError:
                    publish_date = None

            if show_all or (publish_date and start_date <= publish_date <= end_date):
                videos.append({
                    'title': vinfo.get('title', 'Без назви'),
                    'views': vinfo.get('view_count', 0),
                    'likes': vinfo.get('like_count'),
                    'comments_count': vinfo.get('comment_count'),
                    'duration': vinfo.get('duration', 0),
                    'publish_date': publish_date,
                    'thumbnail_url': vinfo.get('thumbnail'),
                    'url': vinfo.get('webpage_url', vurl)
                })
        except Exception as e:
            st.warning(f"Не вдалося обробити відео {vurl}: {e}")
            continue
    return videos


def fetch_comments(video_url, pct_str="100%"):
    """
    Збирає коментарі через YouTube Data API v3.
    Повертає pct_str% коментарів, з лайками і відповідями.
    """
    if not YT_API_KEY:
        st.error("Не заданий ключ API YouTube (YOUTUBE_API_KEY) для отримання коментарів.")
        return []

    video_id_match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|watch\?v=|\&v=)([\w-]{11})", video_url)
    if not video_id_match:
        if re.match(r"^[\w-]{11}$", video_url): # Якщо це вже ID
            video_id = video_url
        else:
            st.error(f"Невірний формат URL або ID відео для отримання коментарів: {video_url}")
            return []
    else:
        video_id = video_id_match.group(1)

    try:
        youtube_service = build("youtube", "v3", developerKey=YT_API_KEY)
        comments_data = []
        next_page_token = None
        max_comments_limit = 1000 # Загальне обмеження на кількість коментарів для одного відео

        while True:
            response = youtube_service.commentThreads().list(
                part="snippet,replies",
                videoId=video_id,
                maxResults=100, # Максимум за один запит
                pageToken=next_page_token,
                textFormat="plainText"
            ).execute()

            for item in response.get("items", []):
                top_level_comment_snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                replies_data = item.get("replies", {}).get("comments", []) # Це вже список коментарів-відповідей

                comments_data.append({
                    "text": top_level_comment_snippet.get("textDisplay", ""),
                    "likes": top_level_comment_snippet.get("likeCount", 0),
                    "replies": replies_data # Зберігаємо список відповідей
                })
                if len(comments_data) >= max_comments_limit:
                    break
            
            if len(comments_data) >= max_comments_limit:
                break

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break
        
        # Обробка відсотка
        if pct_str != "100%":
            try:
                percentage_to_fetch = float(pct_str.strip('%')) / 100
                num_to_return = int(len(comments_data) * percentage_to_fetch)
                # Перемішуємо перед тим як брати зріз, щоб отримати більш випадкову вибірку
                random.shuffle(comments_data) 
                return comments_data[:num_to_return]
            except ValueError:
                st.warning(f"Неправильний формат відсотка: {pct_str}. Повертаю всі коментарі.")
        
        return comments_data
    except Exception as e:
        st.error(f"Помилка при отриманні коментарів через YouTube API: {e}")
        return []


def gpt_sentiment_analysis(comments_texts_list, model="gpt-3.5-turbo"):
    if not client:
        st.error("Клієнт OpenAI не ініціалізований (перевірте API ключ).")
        return {"positive": 0, "neutral": 0, "negative": 0, "error": "OpenAI client not initialized."}

    if not comments_texts_list:
        return {"positive": 0, "neutral": 0, "negative": 0, "error": "Немає текстів коментарів для аналізу."}

    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list:
        return {"positive": 0, "neutral": 0, "negative": 0, "error": "Після очистки не залишилося коментарів для аналізу."}

    sample_comments = clean_comments_list[:100] # Обмежуємо кількість коментарів для одного запиту

    prompt_text = f"""
Проаналізуй наступні ютуб-коментарі українською мовою. Порахуй та поверни приблизну кількість:
- позитивних (positive)
- нейтральних (neutral)
- негативних (negative)

Поверни результат у форматі JSON з ключами "positive", "neutral", "negative". Наприклад:
{{
  "positive": X,
  "neutral": Y,
  "negative": Z
}}

Коментарі для аналізу:
{chr(10).join(sample_comments)}
""".strip()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.2, # Зменшено для більш детермінованого результату підрахунку
            max_tokens=150, # Трохи збільшено про всяк випадок для JSON
        )
        api_response_text = response.choices[0].message.content.strip()

        try:
            # Спроба видалити можливі ```json ... ``` обгортки
            if api_response_text.startswith("```json"):
                api_response_text = api_response_text[7:]
            if api_response_text.endswith("```"):
                api_response_text = api_response_text[:-3]
            
            sentiment_results = json.loads(api_response_text)
            # Перевірка наявності ключів та їх типів
            if not all(k in sentiment_results and isinstance(sentiment_results[k], int) for k in ["positive", "neutral", "negative"]):
                st.warning(f"GPT повернув JSON, але структура або типи даних невірні: {api_response_text}")
                return {"positive": 0, "neutral": 0, "negative": 0, "error": "Invalid JSON structure or data types from GPT."}
            return sentiment_results
        except json.JSONDecodeError:
            st.error(f"Не вдалося розпарсити JSON відповідь від GPT для аналізу тональності: {api_response_text}")
            # Спробуємо старий метод парсингу як запасний варіант, якщо JSON не спрацював
            st.info("Спроба резервного методу парсингу тональності...")
            sentiment_results_fallback = {"positive": 0, "neutral": 0, "negative": 0}
            for line_text in api_response_text.lower().splitlines():
                line_text = line_text.strip()
                if "positive:" in line_text or '"positive":' in line_text:
                    found_numbers = re.findall(r"\d+", line_text)
                    if found_numbers: sentiment_results_fallback["positive"] = int(found_numbers[0])
                elif "neutral:" in line_text or '"neutral":' in line_text:
                    found_numbers = re.findall(r"\d+", line_text)
                    if found_numbers: sentiment_results_fallback["neutral"] = int(found_numbers[0])
                elif "negative:" in line_text or '"negative":' in line_text:
                    found_numbers = re.findall(r"\d+", line_text)
                    if found_numbers: sentiment_results_fallback["negative"] = int(found_numbers[0])
            
            if sum(sentiment_results_fallback.values()) > 0:
                 st.success("Резервний метод парсингу тональності спрацював.")
                 return sentiment_results_fallback
            else:
                 st.error("Резервний метод парсингу тональності також не дав результату.")
                 return {"positive": 0, "neutral": 0, "negative": 0, "error": f"GPT response not parsable as JSON or text: {api_response_text}"}


    except Exception as e:
        st.error(f"Помилка GPT при аналізі тональності: {e}")
        return {"positive": 0, "neutral": 0, "negative": 0, "error": str(e)}


def gpt_comment_summary(comments_texts_list, model="gpt-3.5-turbo"):
    if not client:
        st.error("Клієнт OpenAI не ініціалізований (перевірте API ключ).")
        return "Клієнт OpenAI не ініціалізований."

    if not comments_texts_list:
        return "Немає коментарів для створення підсумку."

    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list:
        return "Після очистки не залишилося коментарів для створення підсумку."

    # Беремо вибірку, але якщо коментарів мало, беремо всі
    sample_size = min(100, len(clean_comments_list))
    sample_for_summary = random.sample(clean_comments_list, sample_size)


    prompt_text = f"""
Тобі надано вибірку з {sample_size} коментарів під відео з YouTube українською мовою.

1. Напиши короткий аналіз найпопулярніших тем або настроїв у цих коментарях (2-3 речення).
2. Узагальни (по 1-2 речення на кожен пункт):
   - Що найбільше сподобалось глядачам (якщо це видно з коментарів)?
   - Що залишило нейтральне враження або було менш обговорюваним?
   - Що викликало негатив чи критику (якщо є)?
3. Зроби короткий висновок (1-2 речення) про загальне враження аудиторії від відео на основі цих коментарів.

Будь ласка, структуруй відповідь чітко.

Коментарі:
{chr(10).join(sample_for_summary)}
""".strip()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.7,
            max_tokens=800,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        st.error(f"Помилка GPT при генерації підсумку коментарів: {e}")
        return f"⚠️ Помилка GPT: {e}"


def format_duration(seconds_total):
    """Форматує тривалість з секунд у HH:MM:SS."""
    if not isinstance(seconds_total, (int, float)) or seconds_total < 0:
        return "00:00:00"
    h = int(seconds_total // 3600)
    m = int((seconds_total % 3600) // 60)
    s = int(seconds_total % 60)
    return f"{h:02}:{m:02}:{s:02}"


# --- Основна логіка програми (тільки "Аналіз відео") ---

video_url_input = st.text_input(
    "URL відео для аналізу:",
    placeholder="Вставте URL YouTube відео...",
    key="main_video_url_input"
)

comments_percentage_options = {"Всі": "100%", "50%": "50%", "25%": "25%", "10%": "10%"}
selected_display_percentage = st.selectbox(
    "Яку частку коментарів аналізувати (випадкова вибірка):",
    list(comments_percentage_options.keys()),
    index=0, 
    key="comments_percentage_selector"
)
percentage_to_fetch_str = comments_percentage_options[selected_display_percentage]

if st.button("🚀 Проаналізувати відео", key="analyze_this_video_button"):
    if not video_url_input:
        st.error("Будь ласка, введіть URL відео для аналізу.")
    else:
        # 1. Отримання інформації про відео за допомогою yt_dlp
        with st.spinner("Збір даних про відео..."):
            video_details = None
            try:
                ydl_opts_video = {
                    'quiet': True,
                    'no_warnings': True,
                    'skip_download': True,
                    'ignoreerrors': True,
                    'extract_flat': False, # Потрібні повні метадані для одного відео
                }
                with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                    video_details = ydl.extract_info(video_url_input, download=False)
            except Exception as e:
                st.error(f"Помилка при отриманні даних відео через yt_dlp: {e}")
                video_details = None # Переконуємось, що video_details None у випадку помилки

        if not video_details:
            st.error(f"Не вдалося отримати інформацію для відео за URL: {video_url_input}")
        else:
            st.subheader(f"Аналіз відео: {video_details.get('title', 'Без назви')}")

            col_thumb, col_info = st.columns([1, 3])
            with col_thumb:
                if video_details.get('thumbnail'):
                    st.image(video_details.get('thumbnail'), width=240, caption="Обкладинка відео")
                else:
                    st.write("🖼️ (немає обкладинки)")

            with col_info:
                st.markdown(f"**Назва:** {video_details.get('title', 'N/A')}")
                st.markdown(f"**Тривалість:** {format_duration(video_details.get('duration', 0))}")
                
                view_count = video_details.get('view_count')
                st.markdown(f"**Перегляди:** {view_count:,}" if isinstance(view_count, int) else "N/A")
                
                likes_count = video_details.get('like_count')
                st.markdown(f"**Лайки:** {likes_count:,}" if isinstance(likes_count, int) else "N/A (приховано або відсутні)")

                comment_count_overall = video_details.get('comment_count') # Це може бути загальна кількість, яку повернув yt-dlp
                st.markdown(f"**Коментарі (за даними yt-dlp):** {comment_count_overall:,}" if isinstance(comment_count_overall, int) else "N/A")

                upload_date_str = video_details.get('upload_date')
                if upload_date_str:
                    try:
                        publish_date_dt = datetime.strptime(upload_date_str, '%Y%m%d').date()
                        st.markdown(f"**Дата публікації:** {publish_date_dt.strftime('%d.%m.%Y')}")
                    except ValueError:
                        st.markdown(f"**Дата публікації:** {upload_date_str} (не вдалося розпарсити формат)")
                else:
                    st.markdown("**Дата публікації:** N/A")

            st.markdown("---")
            st.subheader("📈 Аналіз коментарів до відео")

            with st.spinner(f"Завантаження та аналіз ~{selected_display_percentage} коментарів..."):
                # Виправлено тут: pct_str=percentage_to_fetch_str
                fetched_comments_data = fetch_comments(video_url_input, pct_str=percentage_to_fetch_str)

            if not fetched_comments_data:
                st.info("Коментарі до цього відео не знайдено, їх не вдалося завантажити, або обрано 0%.")
            else:
                st.info(f"Проаналізовано приблизно {len(fetched_comments_data)} коментарів.")
                comment_texts_for_gpt = [
                    comment.get("text", "") for comment in fetched_comments_data
                    if isinstance(comment, dict) and isinstance(comment.get("text"), str) and comment.get("text").strip()
                ]

                if not comment_texts_for_gpt:
                    st.warning("Хоча дані коментарів були завантажені, текстовий вміст для аналізу відсутній або порожній.")
                else:
                    st.markdown("##### Тональність коментарів (за версією GPT):")
                    sentiment_gpt_result = gpt_sentiment_analysis(comment_texts_for_gpt)

                    # Перевіряємо, чи є помилка, і чи є хоч якісь дані
                    if "error" not in sentiment_gpt_result or sum(v for k, v in sentiment_gpt_result.items() if k != "error" and isinstance(v, int)) > 0:
                        # Виключаємо 'error' з підрахунку total_valid_sentiments
                        total_valid_sentiments = sum(v for k, v in sentiment_gpt_result.items() if k != "error" and isinstance(v, int))
                        if total_valid_sentiments == 0 and "error" not in sentiment_gpt_result : # Якщо GPT повернув 0 для всіх, але без помилки
                             st.info("GPT оцінив усі коментарі як такі, що не мають чіткої тональності, або вибірка була замала.")
                        elif total_valid_sentiments > 0:
                            pos_pct = (sentiment_gpt_result.get('positive', 0) / total_valid_sentiments) * 100
                            neu_pct = (sentiment_gpt_result.get('neutral', 0) / total_valid_sentiments) * 100
                            neg_pct = (sentiment_gpt_result.get('negative', 0) / total_valid_sentiments) * 100

                            bar_len = 20
                            pos_bar_len = int(bar_len * pos_pct / 100)
                            neu_bar_len = int(bar_len * neu_pct / 100)
                            neg_bar_len = max(0, bar_len - pos_bar_len - neu_bar_len) # Забезпечуємо, щоб сума не перевищувала bar_len

                            sentiment_display_bar = "🟩" * pos_bar_len + "🟨" * neu_bar_len + "🟥" * neg_bar_len
                            st.markdown(f"{sentiment_display_bar}  Позитивні: {pos_pct:.0f}% | Нейтральні: {neu_pct:.0f}% | Негативні: {neg_pct:.0f}%")
                        
                        if sentiment_gpt_result.get("error"): # Якщо є повідомлення про помилку, показуємо його
                            st.caption(f"Примітка щодо аналізу тональності: {sentiment_gpt_result['error']}")
                    
                    else: # Якщо є помилка і немає даних
                        st.warning(f"Не вдалося отримати аналіз тональності від GPT. {sentiment_gpt_result.get('error', 'Повернуто порожній результат.')}")
                    
                    st.markdown("##### Загальний підсумок коментарів (за версією GPT):")
                    with st.spinner("GPT генерує підсумок коментарів..."):
                        summary_from_gpt = gpt_comment_summary(comment_texts_for_gpt)
                    st.markdown(summary_from_gpt)

                    st.markdown("##### 🔥 Топ-10 найпопулярніших коментарів (за лайками):")
                    comments_with_likes = [c for c in fetched_comments_data if isinstance(c.get('likes'), int)]

                    if not comments_with_likes:
                        st.info("Не знайдено коментарів з інформацією про лайки для відображення топу.")
                    else:
                        top_10_comments = sorted(comments_with_likes, key=lambda x: x['likes'], reverse=True)[:10]
                        for i, comment_detail in enumerate(top_10_comments):
                            st.markdown(f"---\n**{i + 1}.** 👍 {comment_detail['likes']:,} лайків")
                            st.markdown(f"> {comment_detail['text']}")

                            replies_list = comment_detail.get("replies", []) # replies_list тепер список словників коментарів-відповідей
                            if replies_list:
                                # Фільтруємо та сортуємо відповіді
                                valid_replies_list = [
                                    r for r in replies_list 
                                    if isinstance(r, dict) and 
                                       isinstance(r.get("snippet", {}).get("likeCount"), int) and
                                       r.get("snippet", {}).get("textDisplay") # Переконуємось, що є текст відповіді
                                ]
                                sorted_replies_list = sorted(
                                    valid_replies_list,
                                    key=lambda r_item: r_item["snippet"]["likeCount"],
                                    reverse=True
                                )[:3] # Обмеження на 3 найпопулярніші відповіді

                                if sorted_replies_list:
                                    with st.expander(f"💬 Показати до {len(sorted_replies_list)} найпопулярніших відповідей"):
                                        for reply_item in sorted_replies_list:
                                            reply_snippet_data = reply_item.get("snippet", {})
                                            st.markdown(
                                                f"&nbsp;&nbsp;↳ {reply_snippet_data.get('textDisplay', '')}  _(👍 {reply_snippet_data.get('likeCount', 0):,})_"
                                            )
