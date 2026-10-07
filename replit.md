# Python Telegram Bot

A Python Telegram bot that searches TMDB by movie title or genre and replies in Russian with film details and posters.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Start the `Telegram Bot` workflow to run the bot
- `python -m unittest discover -s telegram_bot -v` — run bot message-handler tests
- Telegram Bot secrets: `TELEGRAM_BOT_TOKEN` (BotFather), `TMDB_API_KEY`, and `TELEGRAM_STATS_OWNER_ID`
- User tracking is stored locally in SQLite at `telegram_bot/users.sqlite3`
- API Server env: `DATABASE_URL` — PostgreSQL connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Python 3.13 for the Telegram bot; no additional Python packages
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `telegram_bot/bot.py` — Telegram polling loop, TMDB search, and Russian movie replies
- `telegram_bot/users.sqlite3` — local Telegram user records, created on first run and excluded from Git
- `telegram_bot/README.md` — bot setup and run instructions
- `telegram_bot/test_bot.py` — standard-library unit tests

## Architecture decisions

- The bot uses long polling and the Python standard library so it needs no public webhook URL or third-party package.
- The Telegram token, TMDB key, and stats admin ID are provided through Replit Secrets and are not stored in source.

## Product

The bot welcomes users, searches movie titles, and selects randomized film recommendations from a random TMDB page for recognized genres. Results include Russian-language titles, years, ratings, overviews, and available posters. It tracks unique users and exposes the user list through `/stats` only to the Telegram user ID configured in Replit Secrets as `TELEGRAM_STATS_OWNER_ID`.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- Run only one long-polling instance per bot token. An existing Telegram webhook must be cleared before polling can start.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
