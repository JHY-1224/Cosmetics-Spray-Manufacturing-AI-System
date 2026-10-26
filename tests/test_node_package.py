import json
import stat
from pathlib import Path
import pytest
import yaml
from dotenv import dotenv_values
from quality.config import Settings
from scripts.node_package import generate


def test_independent_host_packages_separate_secrets_pin_center_trust_and_never_overwrite(
    tmp_path, monkeypatch
):
    center, site = tmp_path / "center", tmp_path / "site"
    generate(
        "customer-test",
        center,
        "center",
        "center.example.invalid",
        role="center",
        allowed_sites=["factory-a"],
    )
    key = (center / "center-public-key.txt").read_text().strip()
    generate(
        "customer-test",
        site,
        "site-a",
        "factory-a.example.invalid",
        role="site",
        site_id="factory-a",
        center_url="https://center.example.invalid",
        center_public_key=key,
    )
    # Test dotenv itself, independently of this pytest process's temporary DB overrides.
    for name in Settings.model_fields:
        monkeypatch.delenv("QUALITY_" + name.upper(), raising=False)
    center_settings = Settings(_env_file=center / "runtime.env")
    site_settings = Settings(_env_file=site / "runtime.env")
    assert center_settings.mode == site_settings.mode == "production"
    assert site_settings.package_public_key == key and not site_settings.package_secret
    assert center_settings.s3_secret_key != site_settings.s3_secret_key
    assert center_settings.rabbitmq_url != site_settings.rabbitmq_url
    assert not site_settings.business_send_enabled
    assert site_settings.trusted_origins == ["https://factory-a.example.invalid"]
    assert stat.S_IMODE(site.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in site.iterdir())
    assert len(json.loads((site / "rabbit-definitions.json").read_text())["users"]) == 1
    before = (site / "runtime.env").read_bytes()
    with pytest.raises(FileExistsError):
        generate(
            "customer-test",
            site,
            "site-a",
            "factory-a.example.invalid",
            role="site",
            site_id="factory-a",
            center_url="https://center.example.invalid",
            center_public_key=key,
        )
    assert (site / "runtime.env").read_bytes() == before
    compose = yaml.safe_load(Path("deploy/compose.node.yaml").read_text())
    assert set(compose["services"]) == {
        "db",
        "broker",
        "storage",
        "api",
        "worker",
        "migrate",
        "initialize",
        "init-storage",
        "edge",
    }
    assert "ports" not in compose["services"]["db"] and "ports" not in compose["services"]["broker"]
    assert set(dotenv_values(site / ".env")) >= {"NODE_PROJECT", "NODE_HOST", "NODE_CONFIG_DIR"}
