"""离线核验M02A原件、引用和媒体可解码性；不把技术检查当实听。"""
import hashlib
import json
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import imageio_ffmpeg
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'local/assets/derived/m02a-v1'
REPORT = ROOT / 'local/reports/M02A'


def read_lines(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()]


assets = [a for p in (ROOT / 'local/assets/manifests').glob('*.jsonl') for a in read_lines(p)]
by_id = {a['asset_id']: a for a in assets}
assert len(assets) == len(by_id) == 16
assert len({a['relative_path'] for a in assets}) == len(assets)
results = []
for a in assets:
    assert UUID(a['asset_id']).version == 4
    p = ROOT / a['relative_path']
    assert p.resolve().is_relative_to((ROOT / 'local/assets/raw').resolve())
    assert hashlib.sha256(p.read_bytes()).hexdigest() == a['sha256'], p
    assert all(a[k] for k in ['kind', 'source', 'source_version', 'collected_at', 'source_group_id', 'labels', 'review_status', 'rights'])
    assert set(a['rights']) == {'local_research', 'training', 'redistribution', 'commercial_use'}
    assert all(v['status'] == 'unknown' and v['basis'] and v['checked_at'] for v in a['rights'].values())
    if a['kind'] == 'image':
        with Image.open(p) as im:
            im.verify()
    elif a['kind'] == 'audio':
        with wave.open(str(p), 'rb') as w:
            assert len(w.readframes(w.getnframes())) == w.getnframes() * w.getnchannels() * w.getsampwidth()
    elif a['kind'] == 'video':
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-xerror', '-i', str(p), '-map', '0:v:0',
                        '-map', '0:a:0', '-f', 'null', '-'], capture_output=True, check=True)
    results.append({'asset_id': a['asset_id'], 'relative_path': a['relative_path'], 'bytes': p.stat().st_size,
                    'sha256_match': True, 'media_structure_or_decode': 'pass'})
assert {p.relative_to(ROOT).as_posix() for p in (ROOT / 'local/assets/raw').rglob('*') if p.is_file()} == {a['relative_path'] for a in assets}

tables = {name: read_lines(OUT / (name + '.jsonl')) for name in ['sources', 'evidence', 'scenes', 'audio_candidates', 'art_references']}
assert [len(tables[k]) for k in tables] == [10, 28, 3, 15, 8]
for e in tables['evidence']:
    assert e['rover_branch'] == 'unknown' and e['source_ref']['locator'] and e['context_ref']
    if 'asset_id' in e['context_ref']:
        assert e['context_ref']['asset_id'] in by_id
assert all(s['routing'] == 'unresolved' and s['real_turn_count'] is None and not s['complete_scene_acquired'] for s in tables['scenes'])
assert [(s['speaker_entries'], s['displayed_player_text_count']) for s in tables['scenes']] == [(11, 4), (10, 5), (17, 5)]
for row in tables['audio_candidates'] + tables['art_references']:
    if row['acquired']:
        assert row['asset_id'] in by_id and Path(row['absolute_path']).is_file()
    else:
        assert row['asset_id'] is None
assert sum(c['standalone_audio_acquired'] for c in tables['audio_candidates']) == 5
assert all(c['listened'] is False and c['language'] == 'unknown' and c['mix_status'].endswith('unreviewed') for c in tables['audio_candidates'])
for ref in json.loads((REPORT / 'authority_file_reference.json').read_text(encoding='utf-8')):
    assert hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() == ref['sha256']

result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'result': 'pass', 'raw_files': len(assets),
          'raw_bytes': sum(r['bytes'] for r in results), 'raw_sources': len({a['source']['source_id'] for a in assets}),
          'counts': {k: len(v) for k, v in tables.items()}, 'assets': results,
          'scope': 'SHA256/路径/字段/引用/图片与WAV结构/三视频全程解码；不含实听、游戏分支或训练授权验收',
          'unreviewed_audio_count': 15, 'standalone_wav_count': 5}
(REPORT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'assets'}, ensure_ascii=False))
