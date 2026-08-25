from app.ingestion.extract import get_file_type


def test_recognises_supported_types():
    assert get_file_type("report.PDF") == "pdf"
    assert get_file_type("notes.txt") == "txt"
    assert get_file_type("archive.zip") is None
