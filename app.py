import os
import re
import streamlit as st
from datetime import date, datetime
import yt_dlp
import pandas as pd
# import random # Видалено, оскільки gpt_comment_summary, що його використовував, видалено з цього варіанту
from googleapiclient.discovery import build
from openai import OpenAI

# nltk та SentimentIntensityAnalyzer не потрібні, якщо sentiment_analysis видалено
# sklearn та matplotlib не використовуються

# --- Ключі API (тепер зі Streamlit Secrets) ---
# Передбачається, що в Streamlit Cloud ти створиш Secrets з такими ключами:
# OPENAI_API_KEY = "sk-..."
# YOUTUBE_API_KEY = "AIzaSy..."

OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY")
YT_API_KEY = st.secrets.get("YOUTUBE_API_KEY")

# Ініціалізація клієнта OpenAI
if not OPENAI_API_KEY:
    st.error("Ключ OpenAI API (OPENAI_API_KEY) не знайдено у Streamlit Secrets. Будь ласка, додайте його.")
    # Можна зупинити виконання, якщо ключ не встановлено, або деякі функції не будуть працювати
    # st.stop()
    client = None  # Встановлюємо клієнта в None, щоб уникнути помилок далі
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

# Перевірка ключа YouTube Data API
if not YT_API_KEY:
    # Попередження буде виведене всередині функцій, які його використовують,
    # або можна вивести тут глобальне попередження.
    st.warning(
        "Ключ YouTube Data API (YOUTUBE_API_KEY) не знайдено у Streamlit Secrets. Функції, що його потребують, можуть не працювати.")

# Налаштування сторінки
st.set_page_config(page_title="YouTube Analytics Agent", layout="wide")
st.title("YouTube Analytics Agent")
st.markdown(
    """
    Інтерактивний агент для аналізу YouTube-каналів.
    Використовуйте бічну панель для навігації між сторінками.
    """
)


# --- Функції ---

def extract_channel_id(url):
    """Витягує ID каналу з URL."""
    match = re.search(r"(?:https?://)?(?:www\.)?youtube\.com/@([^/?]+)", url)
    if match:
        return match.group(1)
    # Додамо обробку URL каналу типу /channel/UC...
    match_channel = re.search(r"(?:https?://)?(?:www\.)?youtube\.com/channel/([^/?]+)", url)
    if match_channel:
        return match_channel.group(1)  # Повертаємо ID каналу, а не ім'я користувача
    return None


def fetch_video_metadata(channel_id_or_user, start_date, end_date, limit=10, show_all=False):
    """
    Збирає метадані відео з каналу або від користувача за допомогою yt_dlp.
    Фільтрує за датою.
    """
    # yt_dlp краще працює з URL, що вказує на плейлист завантажень або сторінку відео користувача/каналу
    # Якщо це ID каналу (зазвичай починається з UC), формуємо URL плейлиста завантажень
    if channel_id_or_user.startswith("UC"):
        # Для отримання плейлиста завантажень за ID каналу потрібен YouTube Data API
        # yt_dlp напряму не може отримати плейлист завантажень лише за ID каналу.
        # Простіший варіант для yt_dlp - використовувати URL сторінки відео каналу.
        # Однак, найнадійніше для yt_dlp - це URL сторінки відео користувача (@username/videos)
        # або пряме посилання на плейлист.
        # Оскільки extract_channel_id повертає ім'я користувача для @-посилань,
        # і ID для /channel/-посилань, нам потрібна універсальна логіка.

        # Якщо це ім'я користувача (не починається з UC)
        if not channel_id_or_user.startswith("UC"):
            url = f"https://www.youtube.com/@{channel_id_or_user}/videos"
        else:  # Якщо це ID каналу (UC...)
            # yt_dlp не може напряму взяти всі відео лише за ID каналу без API
            # для отримання playlist ID. Тому тут може бути обмеження.
            # Спробуємо використати URL каналу, хоча це менш надійно для yt_dlp без API.
            st.warning(
                f"Збір відео за ID каналу ({channel_id_or_user}) через yt_dlp може бути неповним без прямого ID плейлиста завантажень. Рекомендується використовувати URL з @username.")
            url = f"https://www.youtube.com/@...{channel_id_or_user}/videos"

    else:  # Якщо це ім'я користувача (не UC...)
        url = f"https://www.youtube.com/@{channel_id_or_user}/videos"

    opts_flat = {
        'ignoreerrors': True,
        'skip_download': True,
        'extract_flat': 'discard_in_playlist',  # 'discard_in_playlist' або True для отримання лише ID
        'dump_single_json': True,
        'playlistend': limit if not show_all else None  # Обмеження, якщо не показуємо всі
    }

    video_ids = []
    try:
        with yt_dlp.YoutubeDL(opts_flat) as ydl:
            info = ydl.extract_info(url, download=False)

        # yt_dlp може повертати 'entries' для плейлистів/каналів
        entries = info.get('entries')
        if entries:  # Якщо це список відео (канал/плейлист)
            video_ids = [e['id'] for e in entries if e and e.get('id')]
        elif info and info.get('id') and info.get(
                'entries') is None:  # Якщо це одне відео (малоймовірно для сторінки каналу)
            video_ids = [info['id']]

    except Exception as e:
        st.error(f"Помилка на етапі flat-парсингу yt_dlp для {url}: {e}")
        return []

    if not video_ids:
        st.warning(f"Не знайдено ID відео для {url} на першому етапі.")
        return []

    # Етап 2: детальний збір метаданих та фільтрація за датами
    opts_det = {'ignoreerrors': True, 'skip_download': True, 'extract_flat': False}  # Тепер extract_flat: False
    videos = []

    # Обмежуємо кількість ID, якщо їх забагато, щоб не перевищити ліміти/час
    # Це особливо важливо, якщо show_all=True і limit не спрацював на першому етапі
    if not show_all and len(video_ids) > limit * 2:  # Невеликий буфер
        video_ids_to_process = video_ids[:limit * 2]
    else:
        video_ids_to_process = video_ids

    for vid_id in video_ids_to_process:
        if not show_all and len(videos) >= limit:  # Якщо вже набрали потрібну кількість
            break
        vurl = f"https://youtu.be/{vid_id}"
        try:
            with yt_dlp.YoutubeDL(opts_det) as ydl:
                vinfo = ydl.extract_info(vurl, download=False)

            if not vinfo:  # Якщо для конкретного ID нічого не повернуто
                continue

            upload_date_str = vinfo.get('upload_date')  # Формат YYYYMMDD
            publish_date = None
            if upload_date_str:
                try:
                    publish_date = datetime.strptime(upload_date_str, '%Y%m%d').date()
                except ValueError:
                    publish_date = None  # Залишаємо None, якщо дата не парситься

            if show_all or (publish_date and start_date <= publish_date <= end_date):
                videos.append({
                    'title': vinfo.get('title', 'Без назви'),
                    'views': vinfo.get('view_count', 0),
                    'likes': vinfo.get('like_count'),  # Може бути None, якщо приховано
                    'comments_count': vinfo.get('comment_count'),  # Може бути None
                    'duration': vinfo.get('duration', 0),
                    'publish_date': publish_date,
                    'thumbnail_url': vinfo.get('thumbnail'),
                    'url': vinfo.get('webpage_url', vurl)  # Надаємо перевагу webpage_url
                })
        except Exception as e:
            st.warning(f"Не вдалося обробити відео {vurl}: {e}")
            continue  # Пропускаємо це відео і переходимо до наступного

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
        if re.match(r"^[\w-]{11}$", video_url):  # Якщо це вже ID
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
        max_comments_limit = 1000  # Загальне обмеження на кількість коментарів для одного відео

        while True:
            response = youtube_service.commentThreads().list(
                part="snippet,replies",
                videoId=video_id,
                maxResults=100,  # Максимум за один запит
                pageToken=next_page_token,
                textFormat="plainText"
            ).execute()

            for item in response.get("items", []):
                top_level_comment = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                replies = item.get("replies", {}).get("comments", [])

                comments_data.append({
                    "text": top_level_comment.get("textDisplay", ""),
                    "likes": top_level_comment.get("likeCount", 0),
                    "replies": replies
                })
                if len(comments_data) >= max_comments_limit:
                    break

            next_page_token = response.get("nextPageToken")
            if not next_page_token or len(comments_data) >= max_comments_limit:
                break

        # Обробка відсотка
        if pct_str != "100%":
            try:
                percentage_to_fetch = float(pct_str.strip('%')) / 100
                num_to_return = int(len(comments_data) * percentage_to_fetch)
                return comments_data[:num_to_return]
            except ValueError:
                st.warning(f"Неправильний формат відсотка: {pct_str}. Повертаю всі коментарі.")

        return comments_data
    except Exception as e:
        st.error(f"Помилка при отриманні коментарів через YouTube API: {e}")
        return []


# ✅ GPT-аналітика тону
def gpt_sentiment_analysis(comments_texts_list, model="gpt-3.5-turbo"):
    if not client:  # Перевірка, чи ініціалізований клієнт OpenAI
        st.error("Клієнт OpenAI не ініціалізований (перевірте API ключ).")
        return {"positive": 0, "neutral": 0, "negative": 0, "error": "OpenAI client not initialized."}

    if not comments_texts_list:
        return {"positive": 0, "neutral": 0, "negative": 0, "error": "Немає текстів коментарів для аналізу."}

    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list:
        return {"positive": 0, "neutral": 0, "negative": 0,
                "error": "Після очистки не залишилося коментарів для аналізу."}

    # Обмежуємо кількість коментарів для одного запиту до GPT
    sample_comments = clean_comments_list[:100]

    prompt_text = f"""
Проаналізуй наступні ютуб-коментарі українською мовою. Порахуй та поверни приблизну кількість:
- позитивних (positive)
- нейтральних (neutral)
- негативних (negative)

Формат виводу має бути таким (лише ці три рядки, де X, Y, Z - цілі числа):
positive: X
neutral: Y
negative: Z

Коментарі для аналізу:
{chr(10).join(sample_comments)}
""".strip()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.3,
            max_tokens=100,
        )
        api_response_text = response.choices[0].message.content.strip()

        sentiment_results = {"positive": 0, "neutral": 0, "negative": 0}
        for line_text in api_response_text.lower().splitlines():
            line_text = line_text.strip()
            if "positive:" in line_text:
                found_numbers = re.findall(r"\d+", line_text)
                if found_numbers: sentiment_results["positive"] = int(found_numbers[0])
            elif "neutral:" in line_text:
                found_numbers = re.findall(r"\d+", line_text)
                if found_numbers: sentiment_results["neutral"] = int(found_numbers[0])
            elif "negative:" in line_text:
                found_numbers = re.findall(r"\d+", line_text)
                if found_numbers: sentiment_results["negative"] = int(found_numbers[0])

        # Перевірка, чи GPT повернув щось схоже на очікуваний результат
        if sum(sentiment_results.values()) == 0 and not any(
                kw in api_response_text.lower() for kw in ["positive", "neutral", "negative"]):
            return {"positive": 0, "neutral": 0, "negative": 0,
                    "error": f"GPT повернув незрозумілу відповідь для аналізу тональності: {api_response_text}"}
        return sentiment_results

    except Exception as e:
        st.error(f"Помилка GPT при аналізі тональності: {e}")
        return {"positive": 0, "neutral": 0, "negative": 0, "error": str(e)}


def gpt_comment_summary(comments_texts_list, model="gpt-3.5-turbo"):
    if not client:  # Перевірка клієнта OpenAI
        st.error("Клієнт OpenAI не ініціалізований (перевірте API ключ).")
        return "Клієнт OpenAI не ініціалізований."

    if not comments_texts_list:
        return "Немає коментарів для створення підсумку."

    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list:
        return "Після очистки не залишилося коментарів для створення підсумку."

    sample_for_summary = random.sample(clean_comments_list, min(100, len(clean_comments_list)))

    prompt_text = f"""
Тобі надано коментарі під відео з YouTube українською мовою.

1. Напиши короткий аналіз найпопулярніших тем або настроїв у цих коментарях (2-3 речення).
2. Узагальни (по 1-2 речення на кожен пункт):
   - Що найбільше сподобалось глядачам (якщо це видно з коментарів)?
   - Що залишило нейтральне враження?
   - Що викликало негатив чи критику?
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
    key="main_video_url_input"  # Унікальний ключ для цього поля вводу
)

# Опції для вибору відсотка коментарів
comments_percentage_options = {"Всі": "100%", "10%": "10%", "50%": "50%"}  # Словник для зручності
selected_display_percentage = st.selectbox(
    "Яку частку коментарів аналізувати:",
    list(comments_percentage_options.keys()),
    index=0,  # "Всі" за замовчуванням
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
                }
                with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                    video_details = ydl.extract_info(video_url_input, download=False)
            except Exception as e:
                st.error(f"Помилка при отриманні даних відео через yt_dlp: {e}")

        if not video_details:
            st.error(f"Не вдалося отримати інформацію для відео за URL: {video_url_input}")
        else:
            st.subheader(f"Аналіз відео: {video_details.get('title', 'Без назви')}")

            col_thumb, col_info = st.columns([1, 3])  # Створюємо колонки для обкладинки та інфо
            with col_thumb:
                if video_details.get('thumbnail'):
                    st.image(video_details.get('thumbnail'), width=240)
                else:
                    st.write("🖼️ (немає обкладинки)")

            with col_info:
                st.markdown(f"**Назва:** {video_details.get('title', 'N/A')}")
                st.markdown(f"**Тривалість:** {format_duration(video_details.get('duration', 0))}")
                st.markdown(f"**Перегляди:** {video_details.get('view_count', 0):,}")  # Форматування числа з комами

                likes_count = video_details.get('like_count')
                st.markdown(
                    f"**Лайки:** {likes_count:,}" if isinstance(likes_count, int) else "N/A (приховано або відсутні)")

                comment_count_overall = video_details.get('comment_count')
                st.markdown(f"**Коментарі (загалом):** {comment_count_overall:,}" if isinstance(comment_count_overall,
                                                                                                int) else "N/A")

                upload_date_str = video_details.get('upload_date')  # Формат YYYYMMDD
                if upload_date_str:
                    try:
                        publish_date_dt = datetime.strptime(upload_date_str, '%Y%m%d').date()
                        st.markdown(f"**Дата публікації:** {publish_date_dt.strftime('%d.%m.%Y')}")
                    except ValueError:
                        st.markdown(f"**Дата публікації:** {upload_date_str} (не вдалося розпарсити формат)")
                else:
                    st.markdown("**Дата публікації:** N/A")

            # 2. Отримання та аналіз коментарів
            st.markdown("---")  # Розділювач
            st.subheader("📈 Аналіз коментарів до відео")

            with st.spinner(f"Завантаження та аналіз {selected_display_percentage} коментарів..."):
                fetched_comments_data = fetch_comments(video_url_input, pct=percentage_to_fetch_str)

            if not fetched_comments_data:
                st.info("Коментарі до цього відео не знайдено, їх не вдалося завантажити, або обрано 0%.")
            else:
                # Витягуємо лише тексти коментарів для передачі в GPT
                comment_texts_for_gpt = [
                    comment.get("text", "") for comment in fetched_comments_data
                    if
                    isinstance(comment, dict) and isinstance(comment.get("text"), str) and comment.get("text").strip()
                ]

                if not comment_texts_for_gpt:
                    st.warning(
                        "Хоча дані коментарів були завантажені, текстовий вміст для аналізу відсутній або порожній.")
                else:
                    # GPT-аналітика тональності
                    st.markdown("##### Тональність коментарів (за версією GPT):")
                    sentiment_gpt_result = gpt_sentiment_analysis(comment_texts_for_gpt)

                    if "error" not in sentiment_gpt_result or sum(
                            v for v in sentiment_gpt_result.values() if isinstance(v, int)) > 0:
                        total_valid_sentiments = sum(
                            v for v in sentiment_gpt_result.values() if isinstance(v, int)) or 1

                        pos_pct = (sentiment_gpt_result.get('positive', 0) / total_valid_sentiments) * 100
                        neu_pct = (sentiment_gpt_result.get('neutral', 0) / total_valid_sentiments) * 100
                        neg_pct = (sentiment_gpt_result.get('negative', 0) / total_valid_sentiments) * 100

                        bar_len = 20  # Довжина прогрес-бару
                        pos_bar_len = int(bar_len * pos_pct / 100)
                        neu_bar_len = int(bar_len * neu_pct / 100)
                        # Залишок для негативних, щоб сума не перевищувала bar_len
                        neg_bar_len = max(0, bar_len - pos_bar_len - neu_bar_len)

                        sentiment_display_bar = "🟩" * pos_bar_len + "🟨" * neu_bar_len + "🟥" * neg_bar_len
                        st.markdown(f"{sentiment_display_bar}  ✅{pos_pct:.0f}% 😐{neu_pct:.0f}% ❌{neg_pct:.0f}%")
                        if "error" in sentiment_gpt_result and sentiment_gpt_result["error"]:
                            st.caption(f"Примітка щодо аналізу тональності: {sentiment_gpt_result['error']}")
                    else:
                        st.warning(
                            f"Не вдалося отримати аналіз тональності від GPT. {sentiment_gpt_result.get('error', 'Повернуто порожній результат.')}")

                    # GPT-аналітика: загальний підсумок коментарів
                    st.markdown("##### Загальний підсумок коментарів (за версією GPT):")
                    summary_from_gpt = gpt_comment_summary(comment_texts_for_gpt)
                    st.markdown(summary_from_gpt)

                    # Топ-10 найпопулярніших коментарів
                    st.markdown("##### 🔥 Топ-10 найпопулярніших коментарів (за лайками):")

                    # Фільтруємо коментарі, де 'likes' є числом
                    comments_with_likes = [c for c in fetched_comments_data if isinstance(c.get('likes'), int)]

                    if not comments_with_likes:
                        st.info("Не знайдено коментарів з інформацією про лайки для відображення топу.")
                    else:
                        top_10_comments = sorted(comments_with_likes, key=lambda x: x['likes'], reverse=True)[:10]
                        for i, comment_detail in enumerate(top_10_comments):
                            st.markdown(f"---\n**{i + 1}.** 👍 {comment_detail['likes']:,} лайків")
                            st.markdown(f"> {comment_detail['text']}")

                            replies_list = comment_detail.get("replies", [])
                            if replies_list:
                                # Фільтруємо та сортуємо відповіді
                                valid_replies_list = [
                                    r for r in replies_list
                                    if isinstance(r, dict) and isinstance(r.get("snippet", {}).get("likeCount"), int)
                                ]
                                sorted_replies_list = sorted(
                                    valid_replies_list,
                                    key=lambda r_item: r_item["snippet"]["likeCount"],
                                    reverse=True
                                )[:5]  # Обмеження на 5 відповідей

                                if sorted_replies_list:
                                    with st.expander(f"💬 Показати до {len(sorted_replies_list)} відповідей"):
                                        for reply_item in sorted_replies_list:
                                            reply_snippet_data = reply_item.get("snippet", {})
                                            st.markdown(
                                                f"↳ {reply_snippet_data.get('textDisplay', '')}  _(👍 {reply_snippet_data.get('likeCount', 0):,})_"
                                            )
