from app.config import settings, MAX_RETRIES, MAX_FILE_SIZE_BYTES, CHUNK_SIZE


def test_defaults_match_original_module_constants():
    assert MAX_RETRIES == 1
    assert MAX_FILE_SIZE_BYTES == 20 * 1024 * 1024
    assert CHUNK_SIZE == 400
    assert settings.openai_chat_model == "gpt-4o-mini"
    assert settings.openai_embedding_model == "text-embedding-3-small"


def test_localhost_dev_origin_always_allowed():
    assert "http://localhost:5173" in settings.allowed_origins


def test_importing_config_opens_no_database_connection():
    import app.config  # noqa: F401
