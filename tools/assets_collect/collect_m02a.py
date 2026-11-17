"""M02A指定来源的一次有限采集；不镜像网页，不登录，不覆盖原件。"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'local/reports/M02A'
REPORT.mkdir(parents=True, exist_ok=True)
HEADERS = {'User-Agent': 'Mozilla/5.0'}
SOURCES = {
    'OFF-01': 'https://www.taptap.cn/moment/588692075025793864',
    'OFF-02': 'https://www.taptap.cn/moment/588679160507925501',
    'OFF-03': 'https://www.taptap.cn/moment/589848658388716609',
    'OFF-04': 'https://www.bilibili.com/opus/977610367052021764',
    'OFF-05': 'https://www.bilibili.com/opus/982414455515119616',
    'PLAT-01': 'https://wiki.kurobbs.com/mc/item/1286814658335739904',
    'COM-01': 'https://wuthering.th.gl/zh-CN/db/stories/story_100012',
    'COM-02': 'https://wuthering.wiki/cn/character_1505.html',
    'COM-03': 'https://genshin-builds.com/cn/wuthering-waves/characters/shorekeeper/info',
    'COM-04': 'https://wiki.biligame.com/wutheringwaves/角色/守岸人',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def get(url, referer=None):
    r = requests.get(url, headers={**HEADERS, **({'Referer': referer} if referer else {})}, timeout=40)
    r.raise_for_status()
    return r


receipts = []
assets = {}
for existing in (ROOT / 'local/assets/manifests').glob('*.jsonl'):
    assets[existing.stem] = [json.loads(line) for line in existing.read_text(encoding='utf-8').splitlines()]
total = sum((ROOT / a['relative_path']).stat().st_size for rows in assets.values() for a in rows)


def acquire(sid, name, url, kind, version, labels, referer=None):
    global total
    path = ROOT / 'local/assets/raw' / sid / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        previous = next((a for a in assets.get(sid, []) if a['relative_path'] == path.relative_to(ROOT).as_posix()), None)
        if previous and previous['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest():
            print(sid, name, '已存在且hash匹配；跳过', flush=True)
            return
        raise FileExistsError(f'原件不覆盖且无匹配清单: {path}')
    r = get(url, referer or SOURCES[sid])
    if total + len(r.content) > 2 * 1024**3:
        raise ValueError('超过M02A首轮2GiB预算')
    path.write_bytes(r.content)
    total += len(r.content)
    record = dict(asset_id=str(uuid4()), kind=kind, relative_path=path.relative_to(ROOT).as_posix(),
                  sha256=hashlib.sha256(r.content).hexdigest(),
                  source={'source_id': sid, 'page_url': SOURCES[sid], 'resource_url': url},
                  source_version=version, collected_at=now(), source_group_id=sid,
                  labels=labels, review_status='acquired_unreviewed',
                  rights={k: {'status': 'unknown', 'basis': '公开可读取不等于已获专项使用授权；未作法律结论',
                              'checked_at': now()} for k in ['local_research', 'training', 'redistribution', 'commercial_use']})
    assets.setdefault(sid, []).append(record)
    manifest = ROOT / 'local/assets/manifests' / (sid + '.jsonl')
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(sid, name, len(r.content), record['sha256'], flush=True)


for sid, url in SOURCES.items():
    try:
        r = get(url)
        soup = BeautifulSoup(r.content.decode('utf-8', errors='replace'), 'html.parser')
        title = soup.title.get_text(' ', strip=True) if soup.title else None
        receipts.append(dict(source_id=sid, url=url, collected_at=now(), http_status=r.status_code,
                             response_bytes=len(r.content), title=title,
                             last_modified=r.headers.get('Last-Modified'), etag=r.headers.get('ETag'),
                             body_saved=False, note='只记录访问元数据；HTTP200不等于正文/音轨可用'))
    except Exception as e:
        receipts.append(dict(source_id=sid, url=url, collected_at=now(), error=str(e), body_saved=False))
(REPORT / 'http_receipts.json').write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding='utf-8')

images = ['FkYllQ0flkuqyen7uofdglr_zGb0', 'FjcMDZasqIixl5LZuGqq6ff2-SgP',
          'FljyAYM8PtByYohvhZPJP9_dhW5H', 'FmBRjIRlWy1VdMJ6vQZi2u1Jr_V0',
          'FjS1NP_lj8JyJs1nYKmhWVRfIEv8']
for index, image_id in enumerate(images, 1):
    acquire('OFF-01', f'official_image_{index:02}.jpg', f'https://img2-tc.tapimg.com/moment/etag/{image_id}.jpg',
            'image', '2024-09-27官方角色展示帖；original_url见公开HTML',
            ['zh-CN', '守岸人', '1.3宣传', 'reference_only'])

videos = [('OFF-02', 'BV17Yx7evEmn'), ('OFF-03', 'BV1gpxxe5E4s'), ('OFF-04', 'BV1wztKeUEBM')]
for sid, bvid in videos:
    view_url = f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}'
    view = get(view_url).json()
    assert view['code'] == 0, view
    data = view['data']
    (REPORT / f'{sid}_video_metadata.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    page = data['pages'][0]
    version = f'{bvid}, P1, cid={page["cid"]}, pubdate={data["pubdate"]}; 360P公开播放源'
    acquire(sid, f'{bvid}_cover.jpg', data['pic'].replace('http:', 'https:'), 'image', version,
            ['守岸人', 'reference_only', 'video_cover'], f'https://www.bilibili.com/video/{bvid}/')
    api = f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={page["cid"]}&qn=16&fnval=0&fourk=0'
    play = get(api, f'https://www.bilibili.com/video/{bvid}/').json()
    assert play['code'] == 0 and len(play['data']['durl']) == 1, play
    (REPORT / f'{sid}_playback_receipt.json').write_text(json.dumps(play, ensure_ascii=False, indent=2), encoding='utf-8')
    acquire(sid, f'{bvid}_P1_360p.mp4', play['data']['durl'][0]['url'], 'video', version,
            ['zh-CN_page_label', 'audio_unlistened', 'official_edited_video', 'P1'], f'https://www.bilibili.com/video/{bvid}/')

# 来自PLAT-01正常浏览器逐项播放后的pageAssets音频资源清单。
voices = [('likes', '1732543504143827885', '守岸人的喜好'),
          ('heart_1', '1732543583764634329', '心声1'),
          ('worries', '1732543503299156274', '守岸人的烦恼'),
          ('aspiration', '1732543503410140006', '抱负和理想'),
          ('first_note', '1732543339421924033', '初奏')]
for name, resource_id, title in voices:
    acquire('PLAT-01', f'{name}_{resource_id}.wav',
            f'https://web-static.kurobbs.com/wiki_audio/{resource_id}.wav', 'audio',
            '图鉴更新时间2025-12-29；编辑梦魣；与游戏当前构建的映射unknown',
            ['zh-CN_menu_label', '守岸人', title, 'voice_menu', 'audio_unlistened'])

print(json.dumps({'raw_files': sum(map(len, assets.values())), 'raw_bytes': total}, ensure_ascii=False))
