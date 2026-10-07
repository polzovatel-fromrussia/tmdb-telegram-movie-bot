"""A small, dependency-free Telegram bot using long polling."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode
from typing import Any

API_ROOT = "https://api.telegram.org"
TMDB_API_ROOT = "https://api.themoviedb.org/3"
TMDB_IMAGE_ROOT = "https://image.tmdb.org/t/p/w500"
POLL_TIMEOUT_SECONDS = 45
REQUEST_TIMEOUT_SECONDS = POLL_TIMEOUT_SECONDS + 10
TMDB_TIMEOUT_SECONDS = 15
RETRY_DELAY_SECONDS = 3
GENRE_RESULTS_LIMIT = 3
STATS_MESSAGE_LIMIT = 3800
USER_DATABASE_PATH = Path(__file__).resolve().with_name("users.sqlite3")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("telegram_bot")
_GENRE_ID_CACHE: dict[str, int] | None = None


class TelegramAPIError(RuntimeError):
    """An API error with any bot token removed from its message."""


class TMDBAPIError(RuntimeError):
    """An API error that never includes the TMDB API key."""


def get_stats_owner_id() -> int | None:
    value = os.getenv("TELEGRAM_STATS_OWNER_ID", "").strip()
    try:
        user_id = int(value)
    except ValueError:
        return None
    return user_id if user_id > 0 else None


def initialize_user_database() -> None:
    with closing(sqlite3.connect(USER_DATABASE_PATH, timeout=10)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()


def record_user(user: Any) -> None:
    if not isinstance(user, dict) or user.get("is_bot") is True:
        return

    user_id = user.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return

    username = user.get("username")
    first_name = user.get("first_name")
    username = username if isinstance(username, str) else None
    first_name = first_name if isinstance(first_name, str) else None

    with closing(sqlite3.connect(USER_DATABASE_PATH, timeout=10)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO telegram_users (user_id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (user_id, username, first_name),
            )


def get_tracked_users() -> list[dict[str, Any]]:
    with closing(sqlite3.connect(USER_DATABASE_PATH, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT user_id, username, first_name
            FROM telegram_users
            ORDER BY first_name COLLATE NOCASE, username COLLATE NOCASE, user_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def format_stats_messages(users: list[dict[str, Any]]) -> list[str]:
    header = f"Всего уникальных пользователей: {len(users)}"
    messages: list[str] = []
    current = header

    for user in users:
        first_name = " ".join(str(user.get("first_name") or "—").splitlines()).strip()
        username = user.get("username")
        username_label = f"@{username}" if isinstance(username, str) and username else "без username"
        line = f"{first_name} — {username_label} — ID: {user['user_id']}"
        if len(current) + len(line) + 1 > STATS_MESSAGE_LIMIT:
            messages.append(current)
            current = f"Список пользователей (продолжение):\n{line}"
        else:
            current = f"{current}\n{line}"

    messages.append(current)
    return messages


def api_request(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_ROOT}/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise TelegramAPIError(
            f"Telegram API {method} returned HTTP {error.code}."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TelegramAPIError(
            f"Telegram API {method} could not be reached. Check the network and retry."
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TelegramAPIError(
            f"Telegram API {method} returned an invalid response."
        ) from None

    if not isinstance(result, dict) or result.get("ok") is not True:
        description = result.get("description", "Unknown API error") if isinstance(result, dict) else "Invalid API response"
        safe_description = str(description).replace(token, "[redacted]")
        raise TelegramAPIError(f"Telegram API {method} failed: {safe_description}")

    return result.get("result")


def tmdb_request(
    api_key: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> Any:
    query = {"api_key": api_key, **(params or {})}
    url = f"{TMDB_API_ROOT}/{endpoint}?{urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=TMDB_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise TMDBAPIError(f"TMDB returned HTTP {error.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TMDBAPIError("TMDB could not be reached. Check the network and retry.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TMDBAPIError("TMDB returned an invalid response.") from None

    if not isinstance(result, dict):
        raise TMDBAPIError("TMDB returned an invalid response.")
    if result.get("success") is False:
        description = str(result.get("status_message", "Unknown API error"))
        raise TMDBAPIError(f"TMDB request failed: {description.replace(api_key, '[redacted]')}")
    return result


GENRE_ALIASES = {
    "action": "Action",
    "боевик": "Action",
    "боевики": "Action",
    "экшн": "Action",
    "экшен": "Action",
    "adventure": "Adventure",
    "приключение": "Adventure",
    "приключения": "Adventure",
    "приключенческий": "Adventure",
    "animation": "Animation",
    "анимация": "Animation",
    "мультфильм": "Animation",
    "мультфильмы": "Animation",
    "мультик": "Animation",
    "мультики": "Animation",
    "comedy": "Comedy",
    "комедия": "Comedy",
    "комедии": "Comedy",
    "комедию": "Comedy",
    "смешные": "Comedy",
    "crime": "Crime",
    "криминал": "Crime",
    "криминальный": "Crime",
    "криминальные": "Crime",
    "documentary": "Documentary",
    "документальный": "Documentary",
    "документальные": "Documentary",
    "документалка": "Documentary",
    "документалки": "Documentary",
    "drama": "Drama",
    "драма": "Drama",
    "драмы": "Drama",
    "драму": "Drama",
    "family": "Family",
    "семейный": "Family",
    "семейные": "Family",
    "семейное": "Family",
    "fantasy": "Fantasy",
    "фэнтези": "Fantasy",
    "history": "History",
    "история": "History",
    "исторический": "History",
    "исторические": "History",
    "horror": "Horror",
    "ужасы": "Horror",
    "ужасов": "Horror",
    "хоррор": "Horror",
    "хорроры": "Horror",
    "ужастик": "Horror",
    "ужастики": "Horror",
    "страшные": "Horror",
    "music": "Music",
    "музыка": "Music",
    "музыкальный": "Music",
    "музыкальные": "Music",
    "mystery": "Mystery",
    "детектив": "Mystery",
    "детективы": "Mystery",
    "детективный": "Mystery",
    "мистика": "Mystery",
    "romance": "Romance",
    "мелодрама": "Romance",
    "мелодрамы": "Romance",
    "мелодраму": "Romance",
    "романтика": "Romance",
    "романтический": "Romance",
    "science fiction": "Science Fiction",
    "sci fi": "Science Fiction",
    "научная фантастика": "Science Fiction",
    "фантастика": "Science Fiction",
    "фантастику": "Science Fiction",
    "триллер": "Thriller",
    "триллеры": "Thriller",
    "thriller": "Thriller",
    "war": "War",
    "война": "War",
    "военный": "War",
    "военные": "War",
    "western": "Western",
    "вестерн": "Western",
    "вестерны": "Western",
}

GENRE_LABELS_RU = {
    "Action": "боевик",
    "Adventure": "приключения",
    "Animation": "анимация",
    "Comedy": "комедия",
    "Crime": "криминал",
    "Documentary": "документальный фильм",
    "Drama": "драма",
    "Family": "семейный фильм",
    "Fantasy": "фэнтези",
    "History": "исторический фильм",
    "Horror": "ужасы",
    "Music": "музыкальный фильм",
    "Mystery": "детектив",
    "Romance": "мелодрама",
    "Science Fiction": "фантастика",
    "Thriller": "триллер",
    "War": "военный фильм",
    "Western": "вестерн",
}

GENRE_FILLER_WORDS = {
    "film", "films", "movie", "movies", "cinema", "genre", "genres",
    "фильм", "фильмы", "фильма", "фильмов", "кино", "жанр", "жанра",
    "жанре", "жанры", "жанров", "покажи", "найди", "посоветуй",
    "предложи", "подбери", "хочу", "посмотреть", "смотреть", "пожалуйста",
    "мне", "какой", "какие", "мне", "ещё", "еще",
}


def normalize_genre_text(text: str) -> str:
    normalized = text.casefold().replace("ё", "е").replace("-", " ")
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def identify_genre(text: str) -> str | None:
    words = normalize_genre_text(text).split()
    query = " ".join(word for word in words if word not in GENRE_FILLER_WORDS)
    return GENRE_ALIASES.get(query)


def get_genre_ids(api_key: str) -> dict[str, int]:
    global _GENRE_ID_CACHE
    if _GENRE_ID_CACHE is None:
        result = tmdb_request(api_key, "genre/movie/list", {"language": "en-US"})
        genres = result.get("genres", [])
        _GENRE_ID_CACHE = {
            genre["name"]: genre["id"]
            for genre in genres
            if isinstance(genre, dict)
            and isinstance(genre.get("name"), str)
            and isinstance(genre.get("id"), int)
        }
    return _GENRE_ID_CACHE


def search_movies(api_key: str, query: str) -> tuple[list[dict[str, Any]], str | None]:
    genre = identify_genre(query)
    if genre is not None:
        genre_id = get_genre_ids(api_key).get(genre)
        if genre_id is None:
            raise TMDBAPIError(f"TMDB did not return the {genre} genre.")

        def discover_page(page: int) -> list[dict[str, Any]]:
            result = tmdb_request(
                api_key,
                "discover/movie",
                {
                    "with_genres": genre_id,
                    "sort_by": "popularity.desc",
                    "include_adult": "false",
                    "language": "ru-RU",
                    "page": page,
                },
            )
            movies = result.get("results", [])
            return [movie for movie in movies if isinstance(movie, dict)]

        page = random.randint(1, 10)
        movies = discover_page(page)
        if not movies and page != 1:
            movies = discover_page(1)
        return random.sample(movies, min(GENRE_RESULTS_LIMIT, len(movies))), genre

    result = tmdb_request(
        api_key,
        "search/movie",
        {
            "query": query,
            "include_adult": "false",
            "language": "ru-RU",
            "page": 1,
        },
    )
    movies = result.get("results", [])
    return [movie for movie in movies if isinstance(movie, dict)][:1], None


def russian_overview(api_key: str, movie: dict[str, Any]) -> str:
    overview = movie.get("overview")
    if isinstance(overview, str) and overview.strip():
        return overview.strip()

    movie_id = movie.get("id")
    if isinstance(movie_id, int):
        try:
            result = tmdb_request(api_key, f"movie/{movie_id}/translations")
        except TMDBAPIError as error:
            logger.warning("Could not retrieve a Russian translation: %s", error)
        else:
            translations = result.get("translations", [])
            russian = [
                item for item in translations
                if isinstance(item, dict) and item.get("iso_639_1") == "ru"
            ]
            russian.sort(key=lambda item: item.get("iso_3166_1") != "RU")
            for translation in russian:
                data = translation.get("data")
                if isinstance(data, dict):
                    translated_overview = data.get("overview")
                    if isinstance(translated_overview, str) and translated_overview.strip():
                        return translated_overview.strip()

    return "Описание на русском языке пока отсутствует в TMDB."


def movie_message(api_key: str, movie: dict[str, Any]) -> str:
    title = movie.get("title") or movie.get("original_title") or "Без названия"
    release_date = movie.get("release_date")
    year = release_date[:4] if isinstance(release_date, str) and re.match(r"^\d{4}", release_date) else "не указан"
    rating = movie.get("vote_average")
    vote_count = movie.get("vote_count", 0)
    if isinstance(rating, (int, float)) and isinstance(vote_count, int) and vote_count > 0:
        rating_text = f"{rating:.1f} / 10"
    else:
        rating_text = "пока нет оценок"
    overview = russian_overview(api_key, movie)
    return (
        f"Название: {title}\n"
        f"Год выхода: {year}\n"
        f"Рейтинг TMDB: {rating_text}\n\n"
        f"Описание: {overview}\n\n"
        "Источник: TMDB (themoviedb.org). Проект не одобрен и не сертифицирован TMDB."
    )


def command_name(text: str) -> str | None:
    """Return a slash command without any optional @botname suffix."""
    first_word = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not first_word.startswith("/"):
        return None
    return first_word[1:].split("@", maxsplit=1)[0].lower()


def command_reply(text: str) -> str | None:
    command = command_name(text)
    if command == "start":
        return (
            "Привет! Я помогу найти фильмы по названию или жанру.\n"
            "Напиши название фильма или, например, «комедия»."
        )
    if command == "help":
        return (
            "Отправь название фильма, чтобы найти его в TMDB, "
            "или напиши жанр — например, «комедия», «ужасы» или «фантастика».\n\n"
            "/start — начать\n"
            "/help — показать эту справку"
        )
    if command is not None:
        return "Неизвестная команда. Напиши /help, чтобы посмотреть справку."
    return None


def send_movie(token: str, api_key: str, chat_id: int, movie: dict[str, Any]) -> None:
    api_request(token, "sendMessage", {"chat_id": chat_id, "text": movie_message(api_key, movie)})

    poster_path = movie.get("poster_path")
    if isinstance(poster_path, str) and poster_path.startswith("/"):
        try:
            api_request(
                token,
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "photo": f"{TMDB_IMAGE_ROOT}{poster_path}",
                },
            )
        except TelegramAPIError as error:
            logger.warning("Could not send the movie poster: %s", error)


def handle_message(token: str, api_key: str, message: dict[str, Any]) -> None:
    sender = message.get("from")
    try:
        record_user(sender)
    except sqlite3.Error as error:
        logger.error("Could not save a Telegram user in SQLite: %s", error)
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if chat_id is not None:
            api_request(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Не удалось сохранить данные пользователя. Попробуй ещё раз позже.",
                },
            )
        return

    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id is None:
        return

    text = message.get("text")
    if not isinstance(text, str):
        api_request(token, "sendMessage", {"chat_id": chat_id, "text": "Отправь название фильма или жанр. Напиши /help для справки."})
        return

    if command_name(text) == "stats":
        sender_id = sender.get("id") if isinstance(sender, dict) else None
        if sender_id != get_stats_owner_id():
            api_request(
                token,
                "sendMessage",
                {"chat_id": chat_id, "text": "У вас нет прав для просмотра статистики."},
            )
            return
        if not isinstance(chat, dict) or chat.get("type") != "private":
            api_request(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Для защиты списка пользователей открой личный чат с ботом и отправь /stats.",
                },
            )
            return
        try:
            stats_messages = format_stats_messages(get_tracked_users())
        except sqlite3.Error as error:
            logger.error("Could not read Telegram user statistics from SQLite: %s", error)
            api_request(
                token,
                "sendMessage",
                {"chat_id": chat_id, "text": "Не удалось получить статистику. Попробуй позже."},
            )
            return
        for stats_message in stats_messages:
            api_request(token, "sendMessage", {"chat_id": chat_id, "text": stats_message})
        return

    response = command_reply(text)
    if response is not None:
        api_request(token, "sendMessage", {"chat_id": chat_id, "text": response})
        return

    try:
        movies, genre = search_movies(api_key, text.strip())
    except TMDBAPIError as error:
        logger.warning("TMDB search failed: %s", error)
        api_request(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": "Не удалось найти фильмы в TMDB. Попробуй ещё раз немного позже."},
        )
        return

    if not movies:
        api_request(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": "Ничего не нашёл. Проверь название фильма или попробуй другой жанр."},
        )
        return

    if genre is not None:
        label = GENRE_LABELS_RU.get(genre, genre)
        api_request(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": f"Популярные фильмы в жанре «{label}»: "},
        )
    for movie in movies:
        send_movie(token, api_key, chat_id, movie)


def run() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Add it in Replit Secrets, then restart this workflow."
        )
    tmdb_api_key = os.getenv("TMDB_API_KEY", "").strip()
    if not tmdb_api_key:
        raise RuntimeError(
            "TMDB_API_KEY is missing. Add it in Replit Secrets, then restart this workflow."
        )
    if get_stats_owner_id() is None:
        raise RuntimeError(
            "TELEGRAM_STATS_OWNER_ID is missing or invalid. Add the numeric Telegram user ID "
            "in Replit Secrets, then restart this workflow."
        )

    initialize_user_database()

    try:
        genres = get_genre_ids(tmdb_api_key)
        logger.info("TMDB connected; loaded %s movie genres.", len(genres))
    except TMDBAPIError as error:
        logger.warning("TMDB startup check failed: %s. Searches will retry when a message arrives.", error)

    bot = api_request(token, "getMe")
    username = bot.get("username", "unknown") if isinstance(bot, dict) else "unknown"
    webhook = api_request(token, "getWebhookInfo")
    if isinstance(webhook, dict) and webhook.get("url"):
        raise RuntimeError(
            "This bot already has a webhook configured. This starter uses long polling; "
            "clear the webhook before starting it."
        )

    logger.info("Telegram bot @%s is running with long polling.", username)
    offset = 0

    while True:
        try:
            updates = api_request(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": POLL_TIMEOUT_SECONDS,
                    "allowed_updates": ["message"],
                },
            )
            for update in updates or []:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    # Acknowledge each update once so a bad message cannot block the queue.
                    offset = max(offset, update_id + 1)
                message = update.get("message")
                if isinstance(message, dict):
                    try:
                        handle_message(token, tmdb_api_key, message)
                    except (TelegramAPIError, TMDBAPIError) as error:
                        logger.warning("Could not process a message: %s", error)
        except TelegramAPIError as error:
            logger.warning("Polling failed: %s Retrying in %s seconds.", error, RETRY_DELAY_SECONDS)
            time.sleep(RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        logger.info("Telegram bot stopped.")
