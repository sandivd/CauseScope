"""Download and install only the checksum-pinned original Coverage.py wheel."""
import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from .adapter import HERE, ROOT, TARGET, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-download', action='store_true')
    args = parser.parse_args()
    lock = json.loads((HERE/'target.lock.json').read_text(encoding='utf-8'))
    wheel = ROOT/'.tools/coverage-download'/lock['filename']
    if not wheel.exists():
        if not args.allow_download:
            raise RuntimeError('Missing artifact; explicit --allow-download required')
        wheel.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(lock['url'], timeout=60) as response:
            wheel.write_bytes(response.read())
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != lock['sha256']:
        raise RuntimeError('Original wheel checksum mismatch')
    if not TARGET.exists():
        subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile',
            '--target',str(TARGET),str(wheel)], check=True)
    verify()
    print('Original Coverage.py 7.16.2 wheel and installed code verified')


if __name__ == '__main__':
    main()
