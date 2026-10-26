import json
import sys
import time

import boto3
from botocore.config import Config

data = json.load(open(sys.argv[1]))
identity = next(r for r in data["identities"] if r["name"] == "bootstrap")["credentials"][0]
client = boto3.client(
    "s3",
    endpoint_url="http://storage:8333",
    aws_access_key_id=identity["accessKey"],
    aws_secret_access_key=identity["secretKey"],
    region_name="us-east-1",
    config=Config(
        signature_version="s3v4", connect_timeout=2, read_timeout=3, s3={"addressing_style": "path"}
    ),
)
for attempt in range(30):
    try:
        client.list_buckets()
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(1)
for bucket in (sys.argv[2:] or ["quality-center", "quality-factory-a", "quality-factory-b"]):
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)
print("配置范围内的对象桶已初始化")
