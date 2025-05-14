import os
import re
import streamlit as st
from datetime import date, datetime
import yt_dlp
import pandas as pd
import random
import json
from googleapiclient.discovery import build
from openai import OpenAI

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
    Інтерактивний агент для аналізу YouTube-каналів та коментарів.
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

    if channel_id_or_user.startswith("UC"):
        url = f"https://www.youtube.com/channel/{channel_id_or_user}/videos"
        st.info(f"Спроба збору відео для ID каналу: {channel_id_or_user}.")
    else:
        url = f"https://www.youtube.com/@{channel_id_or_user}/videos"

    opts_flat = {
        'ignoreerrors': True, 'skip_download': True, 'extract_flat': 'discard_in_playlist',
        'dump_single_json': True, 'playlistend': limit if not show_all else None,
        'quiet': True, 'no_warnings': True,
    }
    video_ids = []
    try:
        with yt_dlp.YoutubeDL(opts_flat) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and 'entries' in info and info['entries']:
            video_ids = [e['id'] for e in info['entries'] if e and e.get('id')]
        elif info and info.get('id') and not info.get('entries'):
             st.warning(f"URL {url} схожий на URL одного відео. Ця функція очікує URL каналу.")
             return []
    except Exception as e:
        st.error(f"Помилка yt_dlp (flat-парсинг) для {url}: {e}")
        return []
    if not video_ids:
        st.warning(f"Не знайдено ID відео для {url} на першому етапі.")
        return []

    opts_det = {
        'ignoreerrors': True, 'skip_download': True, 'extract_flat': False,
        'quiet': True, 'no_warnings': True,
    }
    videos = []
    video_ids_to_process = video_ids
    if not show_all and len(video_ids) > limit * 1.5:
        video_ids_to_process = video_ids[:int(limit * 1.5)]

    for vid_id in video_ids_to_process:
        if not show_all and len(videos) >= limit: break
        vurl = f"https://www.youtube.com/watch?v={vid_id}"
        try:
            with yt_dlp.YoutubeDL(opts_det) as ydl:
                vinfo = ydl.extract_info(vurl, download=False)
            if not vinfo: continue
            upload_date_str = vinfo.get('upload_date')
            publish_date = datetime.strptime(upload_date_str, '%Y%m%d').date() if upload_date_str else None
            if show_all or (publish_date and start_date <= publish_date <= end_date):
                videos.append({
                    'title': vinfo.get('title', 'Без назви'), 'views': vinfo.get('view_count', 0),
                    'likes': vinfo.get('like_count'), 'comments_count': vinfo.get('comment_count'),
                    'duration': vinfo.get('duration', 0), 'publish_date': publish_date,
                    'thumbnail_url': vinfo.get('thumbnail'), 'url': vinfo.get('webpage_url', vurl)
                })
        except Exception as e:
            st.warning(f"Не вдалося обробити відео {vurl}: {e}")
    return videos


def fetch_comments(video_url, pct_str="100%"):
    if not YT_API_KEY:
        st.error("Не заданий ключ API YouTube (YOUTUBE_API_KEY) для отримання коментарів.")
        return []
    video_id_match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|watch\?v=|\&v=)([\w-]{11})", video_url)
    if not video_id_match:
        video_id = video_url if re.match(r"^[\w-]{11}$", video_url) else None
        if not video_id:
            st.error(f"Невірний формат URL або ID відео для коментарів: {video_url}")
            return []
    else:
        video_id = video_id_match.group(1)

    try:
        youtube_service = build("youtube", "v3", developerKey=YT_API_KEY)
        comments_data = []
        next_page_token = None
        max_comments_limit = 1000
        while True:
            response = youtube_service.commentThreads().list(
                part="snippet,replies", videoId=video_id, maxResults=100,
                pageToken=next_page_token, textFormat="plainText"
            ).execute()
            for item in response.get("items", []):
                top_level_comment_snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                replies_data = item.get("replies", {}).get("comments", [])
                comments_data.append({
                    "text": top_level_comment_snippet.get("textDisplay", ""),
                    "likes": top_level_comment_snippet.get("likeCount", 0),
                    "replies": replies_data
                })
                if len(comments_data) >= max_comments_limit: break
            if len(comments_data) >= max_comments_limit or not response.get("nextPageToken"):
                break
            next_page_token = response.get("nextPageToken")
        
        if pct_str != "100%":
            try:
                percentage_to_fetch = float(pct_str.strip('%')) / 100
                num_to_return = int(len(comments_data) * percentage_to_fetch)
                random.shuffle(comments_data) 
                return comments_data[:num_to_return]
            except ValueError:
                st.warning(f"Неправильний формат відсотка: {pct_str}. Повертаю всі коментарі.")
        return comments_data
    except Exception as e:
        st.error(f"Помилка YouTube API при отриманні коментарів: {e}")
        return []

def gpt_sentiment_analysis(comments_texts_list, model="gpt-3.5-turbo"):
    if not client: return {"positive": 0, "neutral": 0, "negative": 0, "error": "OpenAI client not initialized."}
    if not comments_texts_list: return {"positive": 0, "neutral": 0, "negative": 0, "error": "Немає текстів коментарів."}
    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list: return {"positive": 0, "neutral": 0, "negative": 0, "error": "Немає коментарів після очистки."}
    sample_comments = clean_comments_list[:100]
    prompt_text = f"""Проаналізуй наступні ютуб-коментарі українською мовою. Порахуй та поверни приблизну кількість:
- позитивних (positive)
- нейтральних (neutral)
- негативних (negative)
Поверни результат у форматі JSON з ключами "positive", "neutral", "negative". Наприклад: {{"positive": X, "neutral": Y, "negative": Z}}
Коментарі для аналізу:\n{chr(10).join(sample_comments)}""".strip()
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt_text}],
            temperature=0.2, max_tokens=150)
        api_response_text = response.choices[0].message.content.strip()
        try:
            if api_response_text.startswith("```json"): api_response_text = api_response_text[7:]
            if api_response_text.endswith("```"): api_response_text = api_response_text[:-3]
            sentiment_results = json.loads(api_response_text)
            if not all(k in sentiment_results and isinstance(sentiment_results[k], int) for k in ["positive", "neutral", "negative"]):
                return {"positive": 0, "neutral": 0, "negative": 0, "error": f"GPT JSON структура невірна: {api_response_text}"}
            return sentiment_results
        except json.JSONDecodeError: # Резервний парсинг
            st.warning(f"JSON парсинг GPT відповіді не вдався, спроба текстового парсингу: {api_response_text}")
            results_fallback = {"positive": 0, "neutral": 0, "negative": 0}
            for line in api_response_text.lower().splitlines():
                num = re.findall(r"\d+", line)
                if not num: continue
                if "positive" in line: results_fallback["positive"] = int(num[0])
                elif "neutral" in line: results_fallback["neutral"] = int(num[0])
                elif "negative" in line: results_fallback["negative"] = int(num[0])
            if sum(results_fallback.values()) > 0: return results_fallback
            return {"positive": 0, "neutral": 0, "negative": 0, "error": f"GPT відповідь не розпізнана: {api_response_text}"}
    except Exception as e:
        return {"positive": 0, "neutral": 0, "negative": 0, "error": str(e)}

def gpt_topic_analysis_with_sentiment(comments_texts_list, model="gpt-3.5-turbo"):
    """Аналізує коментарі, виділяє 5 тем, їх підсумок та тональність."""
    if not client: return [{"topic": "Помилка", "summary": "Клієнт OpenAI не ініціалізований.", "sentiment": "negative"}]
    if not comments_texts_list: return []
    
    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list: return []

    sample_size = min(200, len(clean_comments_list)) # Більша вибірка для тем
    sample_for_topics = random.sample(clean_comments_list, sample_size)

    prompt_text = f"""
Проаналізуй надану вибірку з {sample_size} коментарів до YouTube-відео українською мовою.
Твоє завдання - визначити 5 основних тем або груп думок, що обговорюються в цих коментарях.
Для кожної з 5 тем:
1.  Дай коротку, влучну назву темі (2-4 слова).
2.  Напиши короткий підсумок цієї теми, що відображає суть обговорень (1-2 речення).
3.  Визнач загальну тональність цієї теми: "positive", "neutral" або "negative".

Поверни результат у форматі JSON-списку об'єктів. Кожен об'єкт має містити ключі "topic" (назва теми), "summary" (підсумок) та "sentiment" (тональність).
Приклад одного об'єкту в списку:
  {{
    "topic": "Якість звуку",
    "summary": "Багато глядачів відзначили високу якість звуку у відео, що покращило загальне враження.",
    "sentiment": "positive"
  }}

Коментарі для аналізу:
{chr(10).join(sample_for_topics)}
""".strip()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.5,
            max_tokens=1500 # Більше токенів для детального аналізу тем
        )
        api_response_text = response.choices[0].message.content.strip()
        try:
            if api_response_text.startswith("```json"): api_response_text = api_response_text[7:]
            if api_response_text.endswith("```"): api_response_text = api_response_text[:-3]
            topics_data = json.loads(api_response_text)
            if not isinstance(topics_data, list) or not all(
                isinstance(t, dict) and all(k in t for k in ["topic", "summary", "sentiment"]) for t in topics_data
            ):
                st.warning(f"GPT повернув JSON для тем, але структура невірна: {api_response_text}")
                return [{"topic": "Помилка парсингу", "summary": "Не вдалося розібрати структуру відповіді від GPT.", "sentiment": "negative"}]
            return topics_data[:5] # Повертаємо максимум 5 тем
        except json.JSONDecodeError:
            st.error(f"Не вдалося розпарсити JSON відповідь від GPT для аналізу тем: {api_response_text}")
            return [{"topic": "Помилка JSON", "summary": "GPT повернув невалідний JSON.", "sentiment": "negative"}]
    except Exception as e:
        st.error(f"Помилка GPT при аналізі тем: {e}")
        return [{"topic": "Помилка GPT", "summary": str(e), "sentiment": "negative"}]

def gpt_analyze_comment_popularity(comment_text, replies_texts_list, model="gpt-3.5-turbo"):
    """Аналізує окремий коментар та його відповіді, щоб припустити причину популярності."""
    if not client: return "Клієнт OpenAI не ініціалізований."
    if not comment_text: return "Текст коментаря порожній."

    replies_str = "\n".join([f"- {r}" for r in replies_texts_list[:5]]) # Беремо до 5 відповідей
    
    prompt_text = f"""
Проаналізуй наступний коментар з YouTube та його відповіді (якщо є). 
Коментар: "{comment_text}"

Відповіді під ним (до 5):
{replies_str if replies_texts_list else "Відповідей немає."}

На основі тексту коментаря та відповідей, коротко (1-3 речення) вислови гіпотезу, чому цей коментар міг стати популярним (набрав багато лайків). 
Зверни увагу на можливі причини: гумор, влучність, згода з більшістю, оригінальність думки, актуальність, провокативність, запитання, що викликало дискусію, тощо.
Відповідь дай українською мовою.
""".strip()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.6,
            max_tokens=200 
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        st.error(f"Помилка GPT при аналізі популярності коментаря: {e}")
        return f"⚠️ Помилка GPT: {e}"


def gpt_comment_summary(comments_texts_list, model="gpt-3.5-turbo"):
    if not client: return "Клієнт OpenAI не ініціалізований."
    if not comments_texts_list: return "Немає коментарів для підсумку."
    clean_comments_list = [c for c in comments_texts_list if isinstance(c, str) and c.strip()]
    if not clean_comments_list: return "Немає коментарів після очистки для підсумку."
    sample_size = min(100, len(clean_comments_list))
    sample_for_summary = random.sample(clean_comments_list, sample_size)
    prompt_text = f"""Тобі надано вибірку з {sample_size} коментарів під відео з YouTube українською мовою.
1. Напиши короткий аналіз найпопулярніших тем або настроїв у цих коментарях (2-3 речення).
2. Узагальни (по 1-2 речення на кожен пункт):
   - Що найбільше сподобалось глядачам (якщо це видно з коментарів)?
   - Що залишило нейтральне враження або було менш обговорюваним?
   - Що викликало негатив чи критику (якщо є)?
3. Зроби короткий висновок (1-2 речення) про загальне враження аудиторії.
Коментарі:\n{chr(10).join(sample_for_summary)}""".strip()
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt_text}],
            temperature=0.7, max_tokens=800)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Помилка GPT (підсумок): {e}"

def format_duration(seconds_total):
    if not isinstance(seconds_total, (int, float)) or seconds_total < 0: return "00:00:00"
    h, rem = divmod(int(seconds_total), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02}:{m:02}:{s:02}"

# --- Основна логіка програми ---
video_url_input = st.text_input("URL відео для аналізу:", placeholder="Вставте URL YouTube відео...", key="main_video_url_input")
comments_percentage_options = {"Всі": "100%", "50%": "50%", "25%": "25%", "10%": "10%"}
selected_display_percentage = st.selectbox(
    "Яку частку коментарів аналізувати (випадкова вибірка):",
    list(comments_percentage_options.keys()), index=0, key="comments_percentage_selector")
percentage_to_fetch_str = comments_percentage_options[selected_display_percentage]

if st.button("🚀 Проаналізувати відео", key="analyze_this_video_button"):
    if not video_url_input:
        st.error("Будь ласка, введіть URL відео для аналізу.")
    else:
        with st.spinner("Збір даних про відео..."):
            video_details = None
            try:
                ydl_opts_video = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'ignoreerrors': True, 'extract_flat': False}
                with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                    video_details = ydl.extract_info(video_url_input, download=False)
            except Exception as e:
                st.error(f"Помилка yt_dlp (дані відео): {e}")
        if not video_details:
            st.error(f"Не вдалося отримати інформацію для відео: {video_url_input}")
        else:
            st.subheader(f"Аналіз відео: {video_details.get('title', 'Без назви')}")
            col_thumb, col_info = st.columns([1, 3])
            with col_thumb:
                st.image(video_details.get('thumbnail', 'https://placehold.co/240x180/CCCCCC/FFFFFF?text=No+Image'), width=240, caption="Обкладинка відео")
            with col_info:
                st.markdown(f"**Назва:** {video_details.get('title', 'N/A')}")
                st.markdown(f"**Тривалість:** {format_duration(video_details.get('duration', 0))}")
                st.markdown(f"**Перегляди:** {video_details.get('view_count', 0):,}")
                st.markdown(f"**Лайки:** {video_details.get('like_count', 0):,}")
                st.markdown(f"**Коментарі (yt-dlp):** {video_details.get('comment_count', 'N/A'):,}")
                upload_date_str = video_details.get('upload_date')
                if upload_date_str:
                    try: st.markdown(f"**Дата публікації:** {datetime.strptime(upload_date_str, '%Y%m%d').strftime('%d.%m.%Y')}")
                    except ValueError: st.markdown(f"**Дата публікації:** {upload_date_str} (не розпарсено)")
                else: st.markdown("**Дата публікації:** N/A")
            st.markdown("---")
            st.subheader("📈 Аналіз коментарів до відео")
            with st.spinner(f"Завантаження та аналіз ~{selected_display_percentage} коментарів..."):
                fetched_comments_data = fetch_comments(video_url_input, pct_str=percentage_to_fetch_str)

            if not fetched_comments_data:
                st.info("Коментарі до цього відео не знайдено, не вдалося завантажити, або обрано 0%.")
            else:
                st.info(f"Завантажено та буде проаналізовано приблизно {len(fetched_comments_data)} коментарів.")
                comment_texts_for_gpt = [c.get("text", "") for c in fetched_comments_data if isinstance(c, dict) and isinstance(c.get("text"), str) and c.get("text").strip()]

                if not comment_texts_for_gpt:
                    st.warning("Текстовий вміст коментарів для аналізу відсутній або порожній.")
                else:
                    # 1. Аналіз тональності загалом
                    st.markdown("##### Тональність коментарів (загальна оцінка GPT):")
                    with st.spinner("GPT аналізує загальну тональність..."):
                         sentiment_gpt_result = gpt_sentiment_analysis(comment_texts_for_gpt)
                    
                    total_sentiments = sum(v for k,v in sentiment_gpt_result.items() if k != "error" and isinstance(v,int))
                    if total_sentiments > 0:
                        pos_pct = (sentiment_gpt_result.get('positive', 0) / total_sentiments) * 100
                        neu_pct = (sentiment_gpt_result.get('neutral', 0) / total_sentiments) * 100
                        neg_pct = (sentiment_gpt_result.get('negative', 0) / total_sentiments) * 100
                        bar_len = 20
                        pos_bar = "🟩" * int(bar_len * pos_pct / 100)
                        neu_bar = "🟨" * int(bar_len * neu_pct / 100)
                        neg_bar = "🟥" * max(0, bar_len - len(pos_bar) - len(neu_bar))
                        st.markdown(f"{pos_bar}{neu_bar}{neg_bar} Поз: {pos_pct:.0f}% | Нейт: {neu_pct:.0f}% | Нег: {neg_pct:.0f}%")
                    if sentiment_gpt_result.get("error"):
                        st.caption(f"Примітка (тональність): {sentiment_gpt_result['error']}")
                    elif total_sentiments == 0 and "error" not in sentiment_gpt_result:
                        st.info("GPT не зміг визначити чітку тональність для наданої вибірки коментарів.")

                    # 2. Аналіз 5 основних тем
                    st.markdown("##### 💬 Основні теми в коментарях (аналіз GPT):")
                    with st.spinner("GPT виділяє основні теми в коментарях..."):
                        topic_analysis_results = gpt_topic_analysis_with_sentiment(comment_texts_for_gpt)
                    
                    if topic_analysis_results:
                        for topic_item in topic_analysis_results:
                            sentiment_color = {"positive": "green", "neutral": "orange", "negative": "red"}.get(topic_item.get("sentiment", "neutral"), "grey")
                            st.markdown(f"""
                            <div style="border-left: 5px solid {sentiment_color}; padding-left: 10px; margin-bottom: 10px;">
                                <strong>Тема: {topic_item.get('topic', 'Не визначено')}</strong> (<span style="color:{sentiment_color};">{topic_item.get('sentiment', 'N/A')}</span>)<br>
                                <em>{topic_item.get('summary', 'Опис відсутній.')}</em>
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.info("Не вдалося виділити основні теми з коментарів.")

                    # 3. Загальний підсумок коментарів
                    st.markdown("##### 📝 Загальний підсумок коментарів (за версією GPT):")
                    with st.spinner("GPT генерує підсумок коментарів..."):
                        summary_from_gpt = gpt_comment_summary(comment_texts_for_gpt)
                    st.markdown(summary_from_gpt)

                    # 4. Топ-10 коментарів з аналізом популярності
                    st.markdown("##### 🔥 Топ-10 найпопулярніших коментарів (за лайками) та аналіз їх популярності:")
                    comments_with_likes = [c for c in fetched_comments_data if isinstance(c.get('likes'), int)]
                    if not comments_with_likes:
                        st.info("Не знайдено коментарів з інформацією про лайки для відображення топу.")
                    else:
                        top_10_comments = sorted(comments_with_likes, key=lambda x: x['likes'], reverse=True)[:10]
                        for i, comment_detail in enumerate(top_10_comments):
                            st.markdown(f"---\n**{i + 1}.** 👍 {comment_detail['likes']:,} лайків")
                            st.markdown(f"> {comment_detail['text']}")
                            
                            # Аналіз популярності окремого коментаря
                            replies_texts = [r.get("snippet", {}).get("textDisplay", "") for r in comment_detail.get("replies", []) if r.get("snippet", {}).get("textDisplay")]
                            with st.spinner(f"GPT аналізує популярність коментаря #{i+1}..."):
                                popularity_analysis = gpt_analyze_comment_popularity(comment_detail['text'], replies_texts)
                            st.markdown(f"<small style='color:grey;'><i><b>Аналіз популярності від GPT:</b> {popularity_analysis}</i></small>", unsafe_allow_html=True)

                            # Відображення відповідей (якщо є)
                            replies_list = comment_detail.get("replies", [])
                            if replies_list:
                                valid_replies = [
                                    r for r in replies_list if isinstance(r, dict) and 
                                    isinstance(r.get("snippet", {}).get("likeCount"), int) and
                                    r.get("snippet", {}).get("textDisplay")]
                                sorted_replies = sorted(valid_replies, key=lambda r_item: r_item["snippet"]["likeCount"], reverse=True)[:3]
                                if sorted_replies:
                                    with st.expander(f"💬 Показати до {len(sorted_replies)} найпопулярніших відповідей"):
                                        for reply_item in sorted_replies:
                                            reply_snippet = reply_item.get("snippet", {})
                                            st.markdown(f"&nbsp;&nbsp;↳ {reply_snippet.get('textDisplay', '')} _(👍 {reply_snippet.get('likeCount', 0):,})_")
