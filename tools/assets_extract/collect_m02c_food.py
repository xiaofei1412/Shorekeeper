"""User-requested two-item follow-up batch; preserve the first M02C delivery."""
import html
import json
import sys
from pathlib import Path
from uuid import uuid4

import requests

from prepare_m02c import ROOT, BATCH as PRIOR_BATCH, REPORT as PRIOR_REPORT, digest, info, now, save, snapshot
from collect_m02c import audio_info, jsonl, read, rights, rows

BATCH = ROOT / 'local/assets/batches/B20261116-M02C-02'
REPORT = PRIOR_REPORT / BATCH.name
PAGE = 'https://wiki.kurobbs.com/mc/item/1286814658335739904'
LOCATOR = '角色语音 / 个性语音 / 中文：唐雅菁'
ENTRIES = [
    {'title': '喜欢的食物', 'resource_url': 'https://web-static.kurobbs.com/wiki_audio/1732543504361278409.wav',
     'observed_at': '2026-11-16T14:03:13.578Z',
     'displayed_source_text': '很久以前，你还在黑海岸的时候，曾用海鲜、时蔬和牛乳给大家煮过一种，喝起来会很暖和的浓汤。一口下去，热意驱散了海风的寒气，连带着身体都变得更有力气……这种汤，后来我自己也试过几次，但总是没你做得好。'},
    {'title': '讨厌的食物', 'resource_url': 'https://web-static.kurobbs.com/wiki_audio/1732543504297819447.wav',
     'observed_at': '2026-11-16T14:03:14.617Z',
     'displayed_source_text': '讨厌……嗯，应该没到这种强烈的程度？不过我的确会避开腌梅子之类的腌制品。盐巴或其他高浓度的调味液，延长了食材原本的保质期限，但因此特有的发酵的味道，更让我想到它们早已失去活性这一点。'}]


def frozen_files():
    legacy = read(PRIOR_REPORT / 'baseline_snapshot.json')
    current = snapshot()
    assert current == legacy['files'], 'Historical frozen inputs changed'
    files = dict(current)
    paths = list(PRIOR_BATCH.rglob('*')) + list(PRIOR_REPORT.iterdir())
    paths += [ROOT / p for p in legacy['documents']]
    paths += [p for p in Path(__file__).parent.iterdir() if p.name != Path(__file__).name]
    for p in paths:
        if p.is_file():
            files[p.relative_to(ROOT).as_posix()] = info(p)
    return files


def prior_audio():
    return rows(ROOT / 'local/assets/manifests/PLAT-01.jsonl') + rows(PRIOR_BATCH / 'manifests/PLAT-01.jsonl')


def prepare():
    assert not BATCH.exists() and not REPORT.exists(), 'Do not refresh or overwrite this batch'
    frozen = frozen_files()
    BATCH.mkdir(parents=True)
    save(REPORT / 'baseline_snapshot.json', {'captured_at': now(), 'files': frozen,
         'scope': 'M02A/B and owner feedback, first M02C batch/reports/scripts, protected documents'})
    save(BATCH / 'derived/menu_observations.json', {'source_id': 'PLAT-01', 'page_url': PAGE,
         'page_title': '守岸人-鸣潮WIKI官网-鸣潮图鉴-库街区', 'updated_at_display': '2025-12-29',
         'latest_editor': '梦魣', 'publisher_class': 'official_platform_edited', 'menu_locator': LOCATOR,
         'method': 'Normal logged-out page; expand each menu row and click its .ico-voice-btn; pageAssets confirms one newly observed audio URL per click.',
         'actual_listening': False, 'displayed_source_text_is_audio_transcription': False,
         'entries': ENTRIES})
    print('second-batch baseline and observed menu references saved')


def collect():
    assert (REPORT / 'baseline_snapshot.json').is_file()
    assert not (BATCH / 'raw').exists() and not (BATCH / 'manifests').exists()
    previous = prior_audio()
    assert len(previous) == 10
    comparisons = [{**a, 'audio_metrics': audio_info(ROOT / a['relative_path'])} for a in previous]
    assets, mappings, receipts = [], [], []
    total = 0
    for entry in ENTRIES:
        url = entry['resource_url']
        assert url not in {a['source']['resource_url'] for a in previous}
        path = BATCH / 'raw/PLAT-01' / url.rsplit('/', 1)[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        started = now()
        with requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': PAGE},
                          stream=True, timeout=(15, 40)) as r:
            r.raise_for_status()
            with path.open('xb') as f:
                for block in r.iter_content(1024 * 1024):
                    total += len(block)
                    assert total + read(PRIOR_BATCH / 'batch.json')['counts']['raw_bytes'] <= 256 * 1024**2
                    f.write(block)
            receipts.append({'title': entry['title'], 'requested_at': started, 'completed_at': now(),
                 'url': url, 'final_url': r.url, 'status': r.status_code,
                 'content_type': r.headers.get('Content-Type'), 'etag': r.headers.get('ETag'),
                 'last_modified': r.headers.get('Last-Modified'), **info(path)})
        asset = {'asset_id': str(uuid4()), 'kind': 'audio', 'relative_path': path.relative_to(ROOT).as_posix(),
            'sha256': digest(path), 'source': {'source_id': 'PLAT-01', 'source_kind': 'public_voice_menu',
            'page_url': PAGE, 'resource_url': url, 'menu_locator': LOCATOR + ' / ' + entry['title']},
            'source_version': '图鉴更新时间2025-12-29；编辑梦魣；与本地游戏构建映射unknown',
            'collected_at': receipts[-1]['completed_at'], 'source_group_id': 'shorekeeper-profile-voice-1.3',
            'labels': ['守岸人_menu_label', 'zh-CN_menu_label', entry['title'], 'audio_unlistened'],
            'review_status': 'pcm_structure_checked_pending_listening', 'rights': rights()}
        metrics = audio_info(path)
        pairs = [{'asset_id': a['asset_id'], 'sha256': a['sha256'], 'audio_metrics': a['audio_metrics'],
                  'same_file_hash': a['sha256'] == asset['sha256'],
                  'same_pcm': a['audio_metrics'] == metrics,
                  'duration_delta_s': abs(a['audio_metrics']['duration_s'] - metrics['duration_s'])} for a in comparisons]
        exact = [p['asset_id'] for p in pairs if p['same_file_hash'] or p['same_pcm']]
        nearby = [p['asset_id'] for p in pairs if p['duration_delta_s'] < 0.25]
        mappings.append({'asset_id': asset['asset_id'], 'relative_path': asset['relative_path'], 'sha256': asset['sha256'],
            'source_kind': 'public_voice_menu', 'source_url': url, 'resource_id': path.stem,
            'title': entry['title'], 'menu_title_status': 'verified', 'menu_character': '守岸人',
            'menu_character_status': 'verified', 'menu_language': '中文：唐雅菁', 'menu_language_status': 'verified',
            'audio_character_status': 'unknown', 'audio_language_status': 'unknown', 'subtitle_status': 'unknown',
            'scene_route_status': 'unknown', 'subtitle': None, 'container_sha256': None, 'internal_path': None,
            'bank_id': None, 'stream_id': None, 'mapping_evidence': 'derived/menu_observations.json',
            'audio_metrics': metrics, 'cross_batch_comparisons': pairs, 'exact_duplicate_of': exact,
            'similar_duration_candidates': nearby, 'possible_duplicate': True if nearby or exact else None,
            'duplicate_status': 'exact_duplicate' if exact else 'no_exact_hash_or_pcm_match_listening_pending',
            'source_group_id': asset['source_group_id'], 'counted_as_verified_independent_utterance': False,
            'review_status': 'pending_listening', 'training_admission': False,
            'note': 'File/PCM mismatch does not rule out reencoded, cropped or semantic duplicates; source group preserved.'})
        assets.append(asset)
        comparisons.append({**asset, 'audio_metrics': metrics})
        print(entry['title'], path.stat().st_size, f'{metrics["duration_s"]:.6f}s', flush=True)
    jsonl(BATCH / 'manifests/PLAT-01.jsonl', assets)
    jsonl(BATCH / 'derived/audio_mapping.jsonl', mappings)
    save(REPORT / 'http_receipts.json', receipts)
    save(BATCH / 'batch.json', {'schema_version': '0.3', 'batch_id': BATCH.name,
        'kind': 'incremental_asset_acquisition', 'created_date': '2026-11-16', 'timezone': 'Asia/Shanghai',
        'created_at': now(), 'purpose': ['owner_requested_liked_and_disliked_food_audio'],
        'authorization': '用户追加要求保存喜欢和讨厌的食物两项；独立新批次，不改首批5项历史交付。',
        'state': 'collected_pending_listening', 'parent_refs': ['local/assets/manifests/PLAT-01.jsonl',
        PRIOR_BATCH.relative_to(ROOT).as_posix() + '/batch.json',
        'local/assets/batches/B20261116-OWNER-01/owner_feedback.json'],
        'records': ['manifests/PLAT-01.jsonl', 'derived/menu_observations.json', 'derived/audio_mapping.jsonl',
                    'derived/review_index.md', 'derived/review_index.html'],
        'counts': {'raw_files': 2, 'public_menu_audio_candidates': 2, 'local_audio_exports': 0,
                   'raw_bytes': total, 'audio_duration_s': sum(m['audio_metrics']['duration_s'] for m in mappings),
                   'verified_independent_new_utterances': 0, 'new_audio_listened': 0},
        'processing': {'python': sys.version, 'requests': requests.__version__,
                       'script': Path(__file__).relative_to(ROOT).as_posix(), 'script_sha256': digest(Path(__file__)),
                       'new_dependencies_installed': 0},
        'dataset_release': None, 'unresolved': ['audio listening', 'text/audio alignment',
                                               'reencoding/semantic duplicates', 'material rights'],
        'local_probe': 'not_repeated; first batch protected-index evidence remains read-only'})
    index = ['# M02C 食物语音增量批次', '', '来自公开菜单，仅新增两项；实际音轨身份、语言与质量待听核。', '']
    players = []
    for a, m in zip(assets, mappings):
        path = ROOT / a['relative_path']
        index.append(f'- [{m["title"]}]({path.as_posix()}) — {m["audio_metrics"]["duration_s"]:.3f}秒。')
        players.append(f'<p>{html.escape(m["title"])}</p><audio controls preload="none" src="../raw/PLAT-01/{path.name}"></audio>')
    index += ['', '页面对应文字见menu_observations.json，仅保存为源文字，未认定与音轨逐字一致；未生成转写或句级时间。',
              '“喜欢的食物”与此前“守岸人的喜好”是两个菜单条目；旧声音反馈未外推至本批。',
              '与既有10项及本批在先项比较文件/PCM hash、格式、时长；独立新台词计数仍为0，未进入训练。']
    (BATCH / 'derived/review_index.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
    (BATCH / 'derived/review_index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>M02C 食物语音</title><body><h1>食物语音新增两项</h1><p>公开菜单补采，待听核；旧反馈不外推。</p>' + ''.join(players) + '</body></html>', encoding='utf-8')


def verify():
    baseline = read(REPORT / 'baseline_snapshot.json')
    assert frozen_files() == baseline['files'], 'Prior delivery changed'
    batch = read(BATCH / 'batch.json')
    manifests = rows(BATCH / 'manifests/PLAT-01.jsonl')
    mappings = rows(BATCH / 'derived/audio_mapping.jsonl')
    observations = read(BATCH / 'derived/menu_observations.json')
    assert observations['entries'] == ENTRIES
    assert observations['actual_listening'] is False and observations['displayed_source_text_is_audio_transcription'] is False
    assert len(manifests) == len({a['asset_id'] for a in manifests}) == len(mappings) == 2
    actual_raw = {p.resolve() for p in (BATCH / 'raw').rglob('*') if p.is_file()}
    assert actual_raw == {(ROOT / a['relative_path']).resolve() for a in manifests}
    previous = [{**a, 'audio_metrics': audio_info(ROOT / a['relative_path'])} for a in prior_audio()]
    exact_duplicates = 0
    for a, m, e, receipt in zip(manifests, mappings, ENTRIES, read(REPORT / 'http_receipts.json')):
        path = (ROOT / a['relative_path']).resolve()
        assert path.is_relative_to((BATCH / 'raw').resolve()) and digest(path) == a['sha256'] == m['sha256'] == receipt['sha256']
        assert a['asset_id'] == m['asset_id'] and a['relative_path'] == m['relative_path']
        assert m['source_url'] == a['source']['resource_url'] == e['resource_url'] == receipt['url']
        assert m['title'] == e['title'] and m['menu_title_status'] == 'verified'
        assert m['audio_metrics'] == audio_info(path) and receipt['status'] == 200
        assert a['source_group_id'] == m['source_group_id'] == 'shorekeeper-profile-voice-1.3'
        assert all(m[k] == 'unknown' for k in ['audio_character_status','audio_language_status','subtitle_status','scene_route_status'])
        assert m['review_status'] == 'pending_listening' and m['training_admission'] is False
        assert m['counted_as_verified_independent_utterance'] is False
        assert all(r['status'] == 'unknown' for r in a['rights'].values())
        assert m['source_url'] not in {p['source']['resource_url'] for p in previous}
        pairs = m['cross_batch_comparisons']
        assert [p['asset_id'] for p in pairs] == [p['asset_id'] for p in previous]
        for pair, old in zip(pairs, previous):
            assert pair['sha256'] == old['sha256'] and pair['audio_metrics'] == old['audio_metrics']
            assert pair['same_file_hash'] == (old['sha256'] == a['sha256'])
            assert pair['same_pcm'] == (old['audio_metrics'] == m['audio_metrics'])
            assert pair['duration_delta_s'] == abs(old['audio_metrics']['duration_s'] - m['audio_metrics']['duration_s'])
        exact = [p['asset_id'] for p in pairs if p['same_file_hash'] or p['same_pcm']]
        nearby = [p['asset_id'] for p in pairs if p['duration_delta_s'] < 0.25]
        assert m['exact_duplicate_of'] == exact and m['similar_duration_candidates'] == nearby
        assert m['possible_duplicate'] is (True if exact or nearby else None)
        exact_duplicates += bool(exact)
        previous.append({**a, 'audio_metrics': m['audio_metrics']})
    total = sum(p.stat().st_size for p in actual_raw)
    duration = sum(m['audio_metrics']['duration_s'] for m in mappings)
    assert total == batch['counts']['raw_bytes'] and duration == batch['counts']['audio_duration_s']
    assert total + read(PRIOR_BATCH / 'batch.json')['counts']['raw_bytes'] <= 256 * 1024**2
    assert batch['counts']['new_audio_listened'] == batch['counts']['verified_independent_new_utterances'] == 0
    assert batch['dataset_release'] is None and batch['processing']['script_sha256'] == digest(Path(__file__))
    assert all((BATCH / p).is_file() for p in batch['records'])
    page = (BATCH / 'derived/review_index.html').read_text(encoding='utf-8')
    assert page.count('<audio controls') == 2 and '<script' not in page and '<iframe' not in page
    result = {'checked_at': now(), 'status': 'passed', 'batch_id': BATCH.name,
        'prior_files_unchanged': len(baseline['files']), 'prior_audio_compared': 10,
        'raw_files': len(actual_raw), 'raw_bytes': total, 'audio_duration_s': duration,
        'local_audio_exports': 0, 'public_menu_audio_candidates': 2, 'pcm_complete_read_count': 2,
        'exact_hash_or_pcm_duplicates': exact_duplicates, 'new_audio_listened': 0,
        'verified_independent_new_utterances': 0, 'new_dependencies_installed': 0,
        'html_playback_test': 'not_run', 'training_files_created': 0, 'dataset_released': False,
        'scope': 'hashes, frozen inputs, complete PCM frames, menu references and duplicate comparison; not listening approval',
        'batch_artifact_sha256': {p.relative_to(BATCH).as_posix(): digest(p) for p in sorted(BATCH.rglob('*')) if p.is_file()}}
    save(REPORT / 'verification.json', result)
    print(json.dumps({k:v for k,v in result.items() if k != 'batch_artifact_sha256'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    {'prepare': prepare, 'collect': collect, 'verify': verify}[sys.argv[1]]()
