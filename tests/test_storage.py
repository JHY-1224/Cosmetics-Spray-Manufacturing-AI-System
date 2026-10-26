import json
import subprocess
import time
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

from quality.config import Settings
from quality.security import digest, token
from quality.storage import Storage
from .test_protocols import free_port


@pytest.fixture(scope="module")
def s3_reference(tmp_path_factory):
    binary = Path("var/tools/weed").resolve()
    if not binary.exists():
        pytest.skip("先运行 scripts/fetch_storage_reference.py；本测试需要经SHA256核验的真实SeaweedFS")
    root = tmp_path_factory.mktemp("seaweedfs")
    access, secret = "q-" + token()[:15], token()
    config = root / "s3.json"
    config.write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "name": "integration",
                        "credentials": [{"accessKey": access, "secretKey": secret}],
                        "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                    }
                ]
            }
        )
    )
    config.chmod(0o600)
    s3port = free_port()
    args = [
        str(binary),
        "server",
        "-ip=127.0.0.1",
        "-ip.bind=127.0.0.1",
        f"-dir={root}",
        "-filer",
        "-s3",
        f"-s3.config={config}",
        f"-s3.port={s3port}",
        "-master.telemetry=false",
        "-master.volumeSizeLimitMB=32",
        "-volume.max=2",
        "-s3.port.iceberg=0",
        "-s3.port.lance=0",
    ]
    for flag in (
        "master.port",
        "master.port.grpc",
        "volume.port",
        "volume.port.grpc",
        "filer.port",
        "filer.port.grpc",
        "s3.port.grpc",
    ):
        args.append(f"-{flag}={free_port()}")
    log = (root / "service.log").open("w")
    process = subprocess.Popen(args, cwd=root, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{s3port}"
    client = boto3.client(
        "s3",
        endpoint_url=url,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            connect_timeout=1,
            read_timeout=2,
            s3={"addressing_style": "path"},
            retries={"max_attempts": 0},
        ),
    )
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError((root / "service.log").read_text()[-4000:])
            try:
                client.create_bucket(Bucket="quality-test")
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise TimeoutError("SeaweedFS did not become ready: " + str(root / "service.log"))
        yield root, url, access, secret, client
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_real_s3_auth_upload_hash_and_remote_read(s3_reference):
    root, url, access, secret, client = s3_reference
    settings = Settings(
        _env_file=None,
        data_dir=root / "spool",
        storage_backend="s3",
        s3_endpoint=url,
        s3_access_key=access,
        s3_secret_key=secret,
        s3_bucket="quality-test",
    )
    storage = Storage(settings)
    body = b"controlled evidence from independent S3 read"
    key = "customer-a/factory-a/evidence.txt"
    assert storage.put(key, body, "text/plain") == "available"
    assert client.head_object(Bucket="quality-test", Key=key)["Metadata"]["sha256"] == digest(body)
    remote = Storage(settings.model_copy(update={"data_dir": root / "fresh-reader"}))
    assert remote.get(key, digest(body)) == body
    wrong = boto3.client(
        "s3",
        endpoint_url=url,
        aws_access_key_id="not-authorized",
        aws_secret_access_key="not-authorized",
        region_name="us-east-1",
        config=Config(signature_version="s3v4", retries={"max_attempts": 0}),
    )
    with pytest.raises(Exception):
        wrong.get_object(Bucket="quality-test", Key=key)


def test_spool_offline_does_not_discard_evidence(s3_reference):
    root, url, access, secret, client = s3_reference
    settings = Settings(
        _env_file=None,
        data_dir=root / "offline-spool",
        storage_backend="s3",
        s3_endpoint=f"http://127.0.0.1:{free_port()}",
        s3_access_key=access,
        s3_secret_key=secret,
        s3_bucket="quality-test",
    )
    body = b"offline evidence retained"
    storage = Storage(settings)
    assert storage.put("customer-a/factory-a/offline.txt", body, "text/plain") == "spooled"
    assert storage.get("customer-a/factory-a/offline.txt", digest(body)) == body
