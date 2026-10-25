"""Acquire only checksum-pinned upstream wheels; do not alter application source."""

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from .adapter import HERE,ROOT,verify


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-download',action='store_true')
    args=parser.parse_args()
    lock=json.loads((HERE/'target.lock.json').read_text(encoding='utf-8'))
    wheels=ROOT/'.tools/black-wheels'; wheels.mkdir(parents=True,exist_ok=True)
    for artifact in lock['wheels']:
        path=wheels/artifact['filename']
        if not path.exists():
            if not args.allow_download:
                raise RuntimeError('Missing wheel; explicit --allow-download is required')
            with urllib.request.urlopen(artifact['url'],timeout=60) as response:
                path.write_bytes(response.read())
        if hashlib.sha256(path.read_bytes()).hexdigest()!=artifact['sha256']:
            raise RuntimeError('Wheel checksum mismatch: '+path.name)
    for release in (lock['bad_version'],lock['fixed_version']):
        directory=ROOT/'.tools'/('black-'+release)
        if not directory.exists():
            subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links',str(wheels),
                            '--target',str(directory),'black=='+release],check=True)
        verify(release)
    print('Both original Black distributions and exact dependency code hashes verified')


if __name__=='__main__':
    main()
