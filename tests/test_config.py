import pytest

from app.config import (
    CHUNK_SIZE,
    DEV_DATABASE_URL,
    LOCAL_DEV_ORIGIN,
    MAX_FILE_SIZE_BYTES,
    MAX_RETRIES,
    ConfigurationError,
    Settings,
    settings,
    validate_for_startup,
)


def test_defaults_match_original_module_constants():
    assert MAX_RETRIES == 1
    assert MAX_FILE_SIZE_BYTES == 20 * 1024 * 1024
    assert CHUNK_SIZE == 400
    assert settings.openai_chat_model == "gpt-4o-mini"
    assert settings.openai_embedding_model == "text-embedding-3-small"


def test_localhost_dev_origin_allowed_in_dev():
    assert LOCAL_DEV_ORIGIN in settings.allowed_origins


def test_localhost_dev_origin_not_allowed_in_prod():
    """An allow-list that silently always contains a localhost entry is one
    nobody audited."""
    prod = Settings(ENVIRONMENT="prod", ALLOWED_ORIGINS="https://example.com")
    assert prod.allowed_origins == ["https://example.com"]


def test_importing_config_opens_no_database_connection():
    import app.config  # noqa: F401


def test_startup_rejects_a_missing_openai_key():
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        validate_for_startup(Settings(OPENAI_API_KEY=""))


def test_startup_rejects_the_dev_database_default_in_prod():
    """Unset DATABASE_URL in production must not silently mean 'localhost'."""
    bad = Settings(
        ENVIRONMENT="prod",
        OPENAI_API_KEY="k",
        DATABASE_URL=DEV_DATABASE_URL,
        AUTH_SECRET_KEY="s",
        AUTH_SIGNUP_SECRET="s",
        ALLOWED_ORIGINS="https://example.com",
    )
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        validate_for_startup(bad)


def test_startup_rejects_tls_disabled_in_prod():
    bad = Settings(
        ENVIRONMENT="prod",
        OPENAI_API_KEY="k",
        DATABASE_URL="postgresql://u:p@db.example.com/x?sslmode=disable",
        AUTH_SECRET_KEY="s",
        AUTH_SIGNUP_SECRET="s",
        ALLOWED_ORIGINS="https://example.com",
    )
    with pytest.raises(ConfigurationError, match="sslmode=disable"):
        validate_for_startup(bad)


def test_startup_reports_every_problem_at_once():
    """An operator fixing a deployment should see the whole list once, not
    rediscover it one restart at a time."""
    with pytest.raises(ConfigurationError) as excinfo:
        validate_for_startup(Settings(ENVIRONMENT="prod", OPENAI_API_KEY=""))
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "AUTH_SECRET_KEY" in message
    assert "ALLOWED_ORIGINS" in message


def test_a_fully_configured_prod_settings_passes():
    good = Settings(
        ENVIRONMENT="prod",
        OPENAI_API_KEY="k",
        DATABASE_URL="postgresql://u:p@db.example.com/x?sslmode=require",
        AUTH_SECRET_KEY="s",
        AUTH_SIGNUP_SECRET="s",
        AUTH_ADMIN_SECRET="s",
        ALLOWED_ORIGINS="https://example.com",
    )
    validate_for_startup(good)  # must not raise
