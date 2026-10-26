"""Integrity of every image, dependency wheel, and manifest in an offline bundle."""

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root):
    root = Path(root).resolve()
    rows = {
        str(p.relative_to(root)): sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "bundle-checksums.json"
    }
    if not rows:
        raise ValueError("离线包为空")
    return rows


def verify(root):
    root = Path(root).resolve()
    rows = json.loads((root / "bundle-checksums.json").read_text())
    if not isinstance(rows, dict) or not rows:
        raise ValueError("缺少离线包校验清单")
    for name, expected in rows.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
            or sha256(path) != expected
        ):
            raise ValueError("离线包文件缺失、路径非法或SHA256不一致：" + name)
    return {"verified_files": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["write", "verify"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.action == "write":
        (args.directory / "bundle-checksums.json").write_text(json.dumps(inventory(args.directory), indent=2))
        print("离线文件完整性清单已生成")
    else:
        print(verify(args.directory))
