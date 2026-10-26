"""Fetch the pinned official SeaweedFS binary; never execute downloaded scripts."""

import hashlib
import json
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "4.48"
URL = f"https://github.com/seaweedfs/seaweedfs/releases/download/{VERSION}/darwin_arm64.tar.gz"
SHA256 = "faa194e72a346755dc4404288b0561eb3c260351702901d8363024d5099bdd7d"


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("此脚本只用于 macOS arm64 参考验证；Linux 使用固定版本容器。")
    target = Path("var/tools")
    target.mkdir(parents=True, exist_ok=True)
    archive = target / f"seaweedfs-{VERSION}.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("xb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("官方资产 SHA256 校验失败；禁止执行")
    with tarfile.open(archive) as tar:
        member = next(m for m in tar.getmembers() if Path(m.name).name == "weed" and m.isfile())
        binary = tar.extractfile(member).read()
        path = target / "weed"
        path.write_bytes(binary)
        path.chmod(0o755)
    (target / "seaweedfs-source.json").write_text(
        json.dumps({"version": VERSION, "source": URL, "sha256": SHA256, "license": "Apache-2.0"}, indent=2)
    )
    print(path.resolve())


if __name__ == "__main__":
    main()
