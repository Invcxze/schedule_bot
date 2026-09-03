import logging

from schedule_bot.logs import attach_file_handler, log_file, tail


def test_log_file_lives_next_to_the_database(tmp_path):
    database_path = tmp_path / "data" / "bot.sqlite3"
    assert log_file(database_path, "bot") == tmp_path / "data" / "bot.log"
    assert log_file(database_path, "celery") == tmp_path / "data" / "celery.log"


def test_attach_file_handler_writes_and_is_idempotent(tmp_path):
    path = tmp_path / "bot.log"
    logger = logging.getLogger("test-attach")
    logger.setLevel(logging.INFO)
    try:
        attach_file_handler(logger, path)
        attach_file_handler(logger, path)  # a second call must not add a duplicate handler
        assert sum(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers) == 1
        logger.info("hello")
        for handler in logger.handlers:
            handler.flush()
        assert "hello" in path.read_text()
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_tail_merges_two_files_in_timestamp_order(tmp_path):
    bot_log = tmp_path / "bot.log"
    celery_log = tmp_path / "celery.log"
    bot_log.write_text(
        "2026-09-01 10:00:00,000 INFO bot: first\n2026-09-01 10:00:02,000 INFO bot: third\n"
    )
    celery_log.write_text("2026-09-01 10:00:01,000 INFO celery: second\n")

    merged = tail([bot_log, celery_log], limit=10).splitlines()
    assert merged == [
        "[bot] 2026-09-01 10:00:00,000 INFO bot: first",
        "[celery] 2026-09-01 10:00:01,000 INFO celery: second",
        "[bot] 2026-09-01 10:00:02,000 INFO bot: third",
    ]


def test_tail_ignores_missing_files_and_respects_limit(tmp_path):
    bot_log = tmp_path / "bot.log"
    lines = (f"2026-09-01 10:00:{i:02},000 INFO bot: line{i}\n" for i in range(5))
    bot_log.write_text("".join(lines))

    assert tail([tmp_path / "missing.log"], limit=10) == ""
    merged = tail([bot_log, tmp_path / "missing.log"], limit=2).splitlines()
    assert len(merged) == 2
    assert merged[-1].endswith("line4")
