import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import bot


class BotMessageTests(unittest.TestCase):
    def test_start_command(self) -> None:
        self.assertIn("фильмы", bot.command_reply("/start"))

    def test_command_with_bot_username(self) -> None:
        self.assertIn("комедия", bot.command_reply("/help@sample_bot"))

    def test_help_command(self) -> None:
        self.assertIn("/start", bot.command_reply("/help"))
        self.assertIn("/help", bot.command_reply("/help"))

    def test_unknown_command(self) -> None:
        self.assertIn("Неизвестная команда", bot.command_reply("/unknown"))

    def test_command_name(self) -> None:
        self.assertEqual("start", bot.command_name("/start"))
        self.assertEqual("help", bot.command_name("/help@sample_bot some text"))
        self.assertIsNone(bot.command_name("ordinary text"))

    def test_genre_detection_handles_russian_context_and_inflection(self) -> None:
        self.assertEqual("Horror", bot.identify_genre("Посоветуй фильмы ужасов"))
        self.assertEqual("Comedy", bot.identify_genre("комедию"))
        self.assertEqual("Science Fiction", bot.identify_genre("sci-fi movies"))

    def test_movie_title_with_genre_word_is_not_treated_as_genre(self) -> None:
        self.assertIsNone(bot.identify_genre("The Comedy of Errors"))

    def test_movie_message_contains_localized_fields(self) -> None:
        movie = {
            "title": "Матрица",
            "release_date": "1999-03-31",
            "vote_average": 8.2,
            "vote_count": 100,
            "overview": "Программист узнаёт, что мир не таков, каким кажется.",
        }

        message = bot.movie_message("unused-test-key", movie)

        self.assertIn("Название: Матрица", message)
        self.assertIn("Год выхода: 1999", message)
        self.assertIn("Рейтинг TMDB: 8.2 / 10", message)
        self.assertIn(movie["overview"], message)

    def test_title_search_uses_russian_language(self) -> None:
        with patch.object(bot, "tmdb_request", return_value={"results": [{"title": "Матрица"}]}) as request:
            movies, genre = bot.search_movies("unused-test-key", "Матрица")

        self.assertEqual("Матрица", movies[0]["title"])
        self.assertIsNone(genre)
        request.assert_called_once_with(
            "unused-test-key",
            "search/movie",
            {
                "query": "Матрица",
                "include_adult": "false",
                "language": "ru-RU",
                "page": 1,
            },
        )

    def test_genre_search_uses_random_page_and_selects_three_movies(self) -> None:
        bot._GENRE_ID_CACHE = {"Comedy": 35}
        results = [{"title": f"Комедия {index}"} for index in range(5)]
        selected = [results[3], results[0], results[4]]
        with (
            patch.object(bot.random, "randint", return_value=7) as random_page,
            patch.object(bot.random, "sample", return_value=selected) as random_movies,
            patch.object(bot, "tmdb_request", return_value={"results": results}) as request,
        ):
            movies, genre = bot.search_movies("unused-test-key", "комедия")

        self.assertEqual("Comedy", genre)
        self.assertEqual(selected, movies)
        random_page.assert_called_once_with(1, 10)
        random_movies.assert_called_once_with(results, 3)
        request.assert_called_once_with(
            "unused-test-key",
            "discover/movie",
            {
                "with_genres": 35,
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "language": "ru-RU",
                "page": 7,
            },
        )

    def test_genre_search_falls_back_when_random_page_is_empty(self) -> None:
        bot._GENRE_ID_CACHE = {"Western": 37}
        page_one_movies = [{"title": "Вестерн 1"}, {"title": "Вестерн 2"}]
        with (
            patch.object(bot.random, "randint", return_value=10),
            patch.object(bot.random, "sample", return_value=list(reversed(page_one_movies))) as random_movies,
            patch.object(
                bot,
                "tmdb_request",
                side_effect=[{"results": []}, {"results": page_one_movies}],
            ) as request,
        ):
            movies, genre = bot.search_movies("unused-test-key", "вестерн")

        self.assertEqual("Western", genre)
        self.assertEqual(list(reversed(page_one_movies)), movies)
        random_movies.assert_called_once_with(page_one_movies, 2)
        self.assertEqual([10, 1], [call_args.args[2]["page"] for call_args in request.call_args_list])

    def test_genre_catalog_is_cached(self) -> None:
        bot._GENRE_ID_CACHE = None
        with patch.object(
            bot,
            "tmdb_request",
            return_value={"genres": [{"id": 35, "name": "Comedy"}, {"id": 18, "name": "Drama"}]},
        ) as request:
            genres = bot.get_genre_ids("unused-test-key")
            second_result = bot.get_genre_ids("unused-test-key")

        self.assertEqual({"Comedy": 35, "Drama": 18}, genres)
        self.assertIs(genres, second_result)
        request.assert_called_once_with(
            "unused-test-key",
            "genre/movie/list",
            {"language": "en-US"},
        )

    def test_message_sends_movie_details_and_poster(self) -> None:
        movie = {
            "title": "Матрица",
            "release_date": "1999-03-31",
            "vote_average": 8.2,
            "vote_count": 100,
            "overview": "Описание на русском.",
            "poster_path": "/matrix.jpg",
        }
        with (
            patch.object(bot, "search_movies", return_value=([movie], None)),
            patch.object(bot, "api_request") as telegram_request,
        ):
            bot.handle_message(
                "unused-telegram-token",
                "unused-test-key",
                {"chat": {"id": 42}, "text": "Матрица"},
            )

        self.assertEqual(
            [
                call(
                    "unused-telegram-token",
                    "sendMessage",
                    {"chat_id": 42, "text": bot.movie_message("unused-test-key", movie)},
                ),
                call(
                    "unused-telegram-token",
                    "sendPhoto",
                    {
                        "chat_id": 42,
                        "photo": "https://image.tmdb.org/t/p/w500/matrix.jpg",
                    },
                ),
            ],
            telegram_request.call_args_list,
        )


class UserTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.stats_owner_id = 999999001
        owner_id_patch = patch.dict(
            os.environ,
            {"TELEGRAM_STATS_OWNER_ID": str(self.stats_owner_id)},
        )
        owner_id_patch.start()
        self.addCleanup(owner_id_patch.stop)
        database_patch = patch.object(
            bot,
            "USER_DATABASE_PATH",
            Path(temp_dir.name) / "users.sqlite3",
        )
        database_patch.start()
        self.addCleanup(database_patch.stop)
        bot.initialize_user_database()

    def test_repeat_messages_update_one_unique_user_record(self) -> None:
        bot.record_user({"id": 1001, "username": "first_name", "first_name": "First"})
        bot.record_user({"id": 1001, "username": "updated_name", "first_name": "Updated"})

        users = bot.get_tracked_users()

        self.assertEqual(1, len(users))
        self.assertEqual(1001, users[0]["user_id"])
        self.assertEqual("updated_name", users[0]["username"])
        self.assertEqual("Updated", users[0]["first_name"])

    def test_non_text_message_is_recorded(self) -> None:
        with patch.object(bot, "api_request") as telegram_request:
            bot.handle_message(
                "unused-telegram-token",
                "unused-test-key",
                {
                    "from": {"id": 1002, "username": "photo_user", "first_name": "Photo"},
                    "chat": {"id": 1002, "type": "private"},
                    "photo": [{"file_id": "photo"}],
                },
            )

        self.assertEqual([1002], [user["user_id"] for user in bot.get_tracked_users()])
        self.assertIn("название фильма", telegram_request.call_args.args[2]["text"])

    def test_stats_is_denied_to_other_users(self) -> None:
        with patch.object(bot, "api_request") as telegram_request:
            bot.handle_message(
                "unused-telegram-token",
                "unused-test-key",
                {
                    "from": {"id": 1003, "first_name": "Not admin"},
                    "chat": {"id": 1003, "type": "private"},
                    "text": "/stats",
                },
            )

        telegram_request.assert_called_once_with(
            "unused-telegram-token",
            "sendMessage",
            {
                "chat_id": 1003,
                "text": "У вас нет прав для просмотра статистики.",
            },
        )

    def test_authorized_stats_lists_unique_users(self) -> None:
        bot.record_user({"id": 1004, "username": "reader", "first_name": "Reader"})
        with patch.object(bot, "api_request") as telegram_request:
            bot.handle_message(
                "unused-telegram-token",
                "unused-test-key",
                {
                    "from": {
                        "id": self.stats_owner_id,
                        "username": "admin",
                        "first_name": "Admin",
                    },
                    "chat": {"id": self.stats_owner_id, "type": "private"},
                    "text": "/stats",
                },
            )

        response = telegram_request.call_args.args[2]["text"]
        self.assertIn("Всего уникальных пользователей: 2", response)
        self.assertIn("@admin", response)
        self.assertIn("@reader", response)
        self.assertIn(str(self.stats_owner_id), response)
        self.assertIn("1004", response)

    def test_owner_must_request_stats_in_private_chat(self) -> None:
        bot.record_user({"id": 1005, "username": "private_user", "first_name": "Private"})
        with patch.object(bot, "api_request") as telegram_request:
            bot.handle_message(
                "unused-telegram-token",
                "unused-test-key",
                {
                    "from": {"id": self.stats_owner_id, "first_name": "Admin"},
                    "chat": {"id": -100, "type": "supergroup"},
                    "text": "/stats",
                },
            )

        telegram_request.assert_called_once()
        response = telegram_request.call_args.args[2]["text"]
        self.assertIn("личный чат", response)
        self.assertNotIn("private_user", response)

    def test_stats_listing_is_split_to_fit_telegram_message_limit(self) -> None:
        users = [
            {"user_id": index, "username": f"user{index}", "first_name": "A long display name"}
            for index in range(300)
        ]

        messages = bot.format_stats_messages(users)

        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= bot.STATS_MESSAGE_LIMIT for message in messages))


if __name__ == "__main__":
    unittest.main()
