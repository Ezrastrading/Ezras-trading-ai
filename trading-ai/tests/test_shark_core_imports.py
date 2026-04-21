def test_shark_dotenv_scheduler_lessons_importable() -> None:
    import trading_ai.shark.dotenv_load  # noqa: F401
    import trading_ai.shark.lessons  # noqa: F401
    import trading_ai.shark.scheduler  # noqa: F401

    from trading_ai.shark import verify_shark_core_modules

    verify_shark_core_modules()
