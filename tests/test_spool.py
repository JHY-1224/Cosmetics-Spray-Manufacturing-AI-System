import base64
import json

from quality.adapters import AdapterConfig, mqtt_read
from quality.config import Settings
from quality.security import digest


def test_archived_spool_does_not_starve_new_pending_messages(tmp_path):
    config = AdapterConfig(endpoint="mqtt://127.0.0.1:19999", topic="quality/backlog")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    directory = (
        tmp_path
        / "adapter-spool"
        / digest([settings.node_id, config.endpoint, config.topic, config.client_id])[:24]
    )
    directory.mkdir(parents=True)
    for index in range(501):
        path = directory / f"{index:04}.json"
        path.write_text(
            json.dumps(
                {
                    "payload": base64.b64encode(
                        json.dumps({"record_id": str(index), "value": index}).encode()
                    ).decode()
                }
            )
        )
        if index < 500:
            path.with_suffix(".done").touch()
    result = mqtt_read(config, settings, True)
    assert result["records"] == [{"record_id": "500", "value": 500}]
