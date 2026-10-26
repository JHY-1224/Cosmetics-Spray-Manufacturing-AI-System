from quality.config import Settings
import pytest


def test_unknown_quality_fields_rejected_without_exposing_values(monkeypatch, tmp_path):
    monkeypatch.setenv("UNRELATED_SERVICE_OPTION", "keep unrelated operating system environment")
    monkeypatch.setenv("QUALITY_CONNECTOR_WMS_SECRET", "secret-only-reference")
    assert Settings(_env_file=None).node_role == "site"
    monkeypatch.setenv("QUALITY_MODEL_APROVED", "sensitive-test-value")
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None)
    assert "QUALITY_MODEL_APROVED" in str(exc.value)
    assert "sensitive-test-value" not in str(exc.value)
    monkeypatch.delenv("QUALITY_MODEL_APROVED")
    source = tmp_path / "config.env"
    source.write_text("QUALITY_DATA_DRI=private-location\n")
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=source)
    assert "QUALITY_DATA_DRI" in str(exc.value) and "private-location" not in str(exc.value)
