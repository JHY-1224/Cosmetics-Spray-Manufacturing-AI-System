"""Copy only lockfile-selected pnpm v11 CAS entries from a read-only local store.

Immutable content is hard-linked when possible; indexes are copied into a separate
SQLite database. Does not download packages, prune stores or alter installed modules.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

import yaml


def prepare(project, source):
    project, source = Path(project).resolve(), Path(source).resolve()
    target = project/'.pnpm-store/v11'
    if target == source or not (source/'index.db').is_file():
        raise ValueError('须提供独立且完整的pnpm v11本机缓存')
    lock = yaml.safe_load((project/'frontend/pnpm-lock.yaml').read_text())
    wanted = {v['resolution']['integrity']+'\t'+name for name,v in lock['packages'].items()
              if v.get('resolution',{}).get('integrity')}
    target.mkdir(parents=True,exist_ok=True)
    source_db = sqlite3.connect('file:'+str(source/'index.db')+'?mode=ro&immutable=1',uri=True)
    rows = [(key,data) for key,data in source_db.execute('SELECT key,data FROM package_index') if key in wanted]
    source_db.close()
    # v11 stores immutable sha512 file digests in its msgpack package indexes.
    digests = {match.decode() for _,data in rows for match in re.findall(rb'(?<![0-9a-f])[0-9a-f]{128}(?![0-9a-f])',data)}
    linked=copied=size=0
    for digest in sorted(digests):
        paths=[source/'files'/digest[:2]/(digest[2:]+suffix) for suffix in ('','-exec')]
        matches=[p for p in paths if p.is_file()]
        if not matches:
            raise ValueError('本机CAS文件缺失；不能宣称离线包完整')
        for path in matches:
            if hashlib.sha512(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('本机CAS内容与摘要不同，拒绝使用')
            dest=target/path.relative_to(source)
            dest.parent.mkdir(parents=True,exist_ok=True)
            size+=path.stat().st_size
            if dest.exists():
                if hashlib.sha512(dest.read_bytes()).hexdigest()!=digest:
                    raise ValueError('项目缓存存在冲突，禁止覆盖')
                continue
            try:
                os.link(path,dest)
                linked+=1
            except OSError:
                shutil.copy2(path,dest)
                copied+=1
    with sqlite3.connect(target/'index.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS package_index (key TEXT PRIMARY KEY,data BLOB NOT NULL) WITHOUT ROWID')
        for key,data in rows:
            prior=db.execute('SELECT data FROM package_index WHERE key=?',(key,)).fetchone()
            if prior and prior[0]!=data:
                raise ValueError('索引已存在不同内容，拒绝覆盖')
            db.execute('INSERT OR IGNORE INTO package_index VALUES (?,?)',(key,data))
    return {'platform':'current local cache only; not a Linux/x86 acceptance',
            'packages':len(rows),'lock_entries':len(wanted),'content_files':len(digests),
            'content_bytes':size,'new_hardlinks':linked,'copied_files':copied,
            'missing_entries':len(wanted)-len(rows),'network_used':False,
            'verification':'must run pnpm install --offline --frozen-lockfile in a new directory'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--project',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    print(json.dumps(prepare(args.project,args.source),ensure_ascii=False,indent=2))
