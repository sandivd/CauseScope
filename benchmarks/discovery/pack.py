"""Losslessly compress raw trial JSON after measurement; preserve original hashes."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def pack(directory: Path) -> dict:
    directory=directory.resolve()
    summary_path=directory/'summary.json'
    summary=json.loads(summary_path.read_text(encoding='utf-8'))
    originals=[]
    for record in summary['records']:
        path=(directory/record['evidence']).resolve()
        if not path.is_relative_to(directory) or path.is_symlink():
            raise ValueError('Unsafe evidence path')
        if path.suffix=='.gz':
            continue
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['evidence_sha256']:
            raise ValueError('Raw evidence hash mismatch')
        compressed=gzip.compress(raw,compresslevel=9,mtime=0)
        destination=path.with_suffix('.json.gz')
        destination.write_bytes(compressed)
        assert gzip.decompress(compressed)==raw
        record['uncompressed_sha256']=record['evidence_sha256']
        record['evidence_sha256']=hashlib.sha256(compressed).hexdigest()
        record['evidence']=destination.relative_to(directory).as_posix()
        originals.append(path)
    summary['artifact_packaging']={'compression':'gzip','mtime':0,'uncompressed_hashes_retained':True,
                                   'measurement_fields_unchanged':True}
    summary_path.write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    for path in originals:
        path.unlink()
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    result=pack(args.directory)
    print('Losslessly packed',len(result['records']),'trial records; original byte hashes retained')
