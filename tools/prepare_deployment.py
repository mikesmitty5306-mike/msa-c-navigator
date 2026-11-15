"""Validate the committed public snapshot and optionally publish its public VAPID key."""
from datetime import datetime,timezone
import json
import base64
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from msac.export import canonical
from msac.export.validate import validate_export


def prepare(folder, public_key=None):
    folder = Path(folder)
    validate_export(folder)
    current = json.loads((folder/'meta.json').read_text(encoding='utf-8'))
    if not current['counts']['articles'] or not current['counts']['trials']:
        raise ValueError('Refusing to deploy an empty research export; keep the last good site')
    if public_key:
        if not isinstance(public_key,str) or not public_key.strip():
            raise ValueError('VAPID_PUBLIC_KEY must be a nonempty public key')
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            raw = base64.urlsafe_b64decode(public_key+'='*(-len(public_key)%4))
            if len(raw)!=65 or raw[0]!=4:
                raise ValueError()
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw)
        except (ValueError,TypeError):
            raise ValueError('VAPID_PUBLIC_KEY must be the uncompressed P-256 public key, never a private key') from None
        path = folder/'meta.json'
        meta = json.loads(path.read_text(encoding='utf-8'))
        if meta.get('push_public_key')!=public_key:
            meta['push_public_key'] = public_key
            stamp = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
            meta['build_id'] = meta['generated_at'] = stamp
            pending = folder/'.deployment-meta.tmp'
            try:
                pending.write_bytes(canonical(meta))
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
    validate_export(folder)


if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'public-data')
    prepare(parser.parse_args().data,os.environ.get('VAPID_PUBLIC_KEY'))
