"""Audit a prepared snapshot, printing paths/counts but never credential values."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def main():
    p=argparse.ArgumentParser()
    p.add_argument('snapshot',type=Path)
    p.add_argument('--private-env',type=Path,help='Optional local comparison; contents are never emitted')
    args=p.parse_args()
    root=args.snapshot.resolve()
    manifest=json.loads((root/'PUBLIC_RELEASE_MANIFEST.json').read_text())
    secrets=[]
    if args.private_env:
        from dotenv import dotenv_values
        for key,value in dotenv_values(args.private_env).items():
            if re.search(r'KEY|TOKEN|SECRET|PASSWORD',key) and value and len(value)>=12:
                secrets.append(value.encode())
    blocked={'var','local_training','node_modules','.data','.git','关键对接需求文档','configs-private','local-only'}
    patterns=[rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{40,}',rb'sk-[A-Za-z0-9]{24,}',
              rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/]{30,}']
    issues=[]
    listed={item['path']:item for item in manifest['files']}
    for path in sorted(root.rglob('*')):
        if not path.is_file(): continue
        name=str(path.relative_to(root))
        if name=='PUBLIC_RELEASE_MANIFEST.json': continue
        blob=path.read_bytes()
        if (path.is_symlink() or any(part in blocked or part.startswith('.venv') for part in path.relative_to(root).parts)
            or path.name=='.env' or path.suffix in ('.safetensors','.db','.pem','.key','.pt','.pth')):
            issues.append({'path':name,'reason':'private/runtime/weight path'})
        if name not in listed or hashlib.sha256(blob).hexdigest()!=listed[name]['sha256']:
            issues.append({'path':name,'reason':'manifest mismatch'})
        if any(secret in blob for secret in secrets) or any(re.search(pattern,blob) for pattern in patterns):
            issues.append({'path':name,'reason':'credential-like content; value suppressed'})
    missing=[n for n in listed if not (root/n).is_file()]
    print(json.dumps({'checked_files':len(listed),'matched_private_values':len(secrets),
                      'issues':issues,'missing':missing,'status':'passed' if not issues and not missing else 'failed'},ensure_ascii=False,indent=2))
    if issues or missing: raise SystemExit(1)


if __name__=='__main__': main()
