import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quality.cli import initialize, migrate, seed_demo
from quality.config import Settings
from quality.security import token

settings = Settings()
if settings.mode == "production":
    raise SystemExit("生产拒绝演示初始化")
migrate(settings)
path = settings.data_dir / "demo-admin.json"
if not path.exists():
    password = token()
    initialize(settings, "admin", password)
    path.write_text(json.dumps({"username": "admin", "password": password}))
    path.chmod(0o600)
seed_demo(settings)
print("演示初始化完成；随机账号凭据位于 var/demo-admin.json 和 var/demo-credentials.json，文件权限0600。")
