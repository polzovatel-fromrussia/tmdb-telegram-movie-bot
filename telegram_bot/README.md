# Python Telegram Bot

A Telegram movie-search bot that uses Python's standard library and the TMDB and Telegram APIs. It uses long polling, so it does not need a public webhook URL or third-party Python packages.

## What it does

- `/start` and `/help` explain how to search in Russian.
- Movie titles return the closest TMDB result with its Russian title, release year, rating, Russian overview, and poster when available.
- Recognized genres use a random TMDB page from 1–10 and return up to three randomly selected films with the same details and posters. If the selected page is empty, the bot falls back to page 1.
- Every message updates one SQLite record per Telegram user ID, including username and first name.
- `/stats` lists tracked users only for the Telegram user ID configured in `TELEGRAM_STATS_OWNER_ID`, and only in a private chat with the bot. Other users receive a permission-denied reply.
- Non-text messages get a short explanation.

Movie details include TMDB attribution. This bot is not endorsed or certified by TMDB.
The local SQLite file is created automatically and ignored by Git.

## Run it

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Add the token to this Replit project's **Secrets** as `TELEGRAM_BOT_TOKEN`.
3. Add a TMDB API key as `TMDB_API_KEY`.
4. Add the Telegram numeric user ID allowed to use `/stats` as `TELEGRAM_STATS_OWNER_ID`.
5. Start the **Telegram Bot** workflow.
5. Open the bot in Telegram and send `/start`.

You can also run it from a shell with `python telegram_bot/bot.py` after setting `TELEGRAM_BOT_TOKEN`, `TMDB_API_KEY`, and `TELEGRAM_STATS_OWNER_ID`.

Try `Матрица`, `комедия`, or `фильмы ужасов`. Keep only one copy of this long-polling bot running for a given Telegram token. If a webhook is already configured, the bot will stop and explain that it must be cleared before long polling can start.

## Extend it

Add or adjust genre names in `GENRE_ALIASES` in `bot.py`. The Telegram and TMDB credentials and stats admin ID are read from the environment and are never included in source control or logs.
