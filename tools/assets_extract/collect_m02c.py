"""One bounded fallback batch after the fixed pak's protected-index probe."""
import hashlib
import html
import json
import wave
from pathlib import Path
from uuid import uuid4

import requests

from prepare_m02c import ROOT, BATCH, REPORT, digest, info, now, save


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    assert not path.exists(), path
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')


def audio_info(path):
    with wave.open(str(path), 'rb') as w:
        frames = w.readframes(w.getnframes())
        assert len(frames) == w.getnframes() * w.getnchannels() * w.getsampwidth()
        assert w.getcomptype() == 'NONE'
        return {'codec': 'PCM', 'sample_rate': w.getframerate(), 'channels': w.getnchannels(),
                'sample_width_bytes': w.getsampwidth(), 'frames': w.getnframes(),
                'duration_s': w.getnframes() / w.getframerate(),
                'pcm_sha256': hashlib.sha256(frames).hexdigest(), 'full_frames_read': True}


def rights():
    return {k: {'status': 'unknown', 'basis': '读取或下载不代表训练/分发许可；未作法律结论',
                'checked_at': now()} for k in ['local_research', 'training', 'redistribution', 'commercial_use']}


def main():
    probe = read(BATCH / 'derived/probe_tool_result.json')
    assert probe['index_status'] == 'blocked'
    assert probe['exception_type'].endswith('InvalidAesKeyException')
    assert probe['header']['EncryptedIndex'] is True
    assert not (BATCH / 'manifests').exists(), 'Do not append or overwrite a completed acquisition'
    observations = read(BATCH / 'derived/menu_observations.json')
    old = rows(ROOT / 'local/assets/manifests/PLAT-01.jsonl')
    feedback_path = ROOT / 'local/assets/batches/B20261116-OWNER-01/owner_feedback.json'
    feedback = read(feedback_path)
    old_by_id = {a['asset_id']: a for a in old}
    for ref in feedback['referenced_audio_assets']:
        asset = old_by_id[ref['asset_id']]
        assert ref['source_sha256'] == asset['sha256'] == digest(ROOT / asset['relative_path'])
    previous = []
    for a in old:
        previous.append({'asset_id': a['asset_id'], 'sha256': a['sha256'],
                         'title_labels': a['labels'], **audio_info(ROOT / a['relative_path'])})
    containers = []
    for inp in read(BATCH / 'derived/input_snapshot.json')['inputs']:
        p = Path(inp['copy']['path'])
        containers.append({'asset_id': str(uuid4()), 'kind': 'container' if p.suffix == '.pak' else 'signature',
            'relative_path': p.relative_to(ROOT).as_posix(), 'sha256': digest(p),
            'source': {'source_id': 'GAME-LOCAL', 'source_kind': 'local_installation_copy',
                       'original_path': inp['before']['path']},
            'source_version': 'resource root 3.8.0; MountResource.txt patch 3.8.11; no internal build mapping',
            'collected_at': now(), 'source_group_id': 'shorekeeper-local-3.8.0-role-zh',
            'labels': ['fixed_input', 'internal_identity_unknown'],
            'review_status': 'copy_hash_verified_index_blocked' if p.suffix == '.pak' else 'copy_hash_verified_signature_not_validated',
            'rights': rights()})
    acquired, mapping, receipts = [], [], []
    total = sum(Path(i['copy']['path']).stat().st_size for i in read(BATCH / 'derived/input_snapshot.json')['inputs'])
    assert 0 < len(observations['entries']) <= 5
    for entry in observations['entries']:
        url = entry['resource_url']
        assert url not in {a['source']['resource_url'] for a in old}
        target = BATCH / 'raw/PLAT-01' / url.rsplit('/', 1)[1]
        assert not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        started = now()
        with requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': observations['page_url']},
                          stream=True, timeout=(15, 40)) as r:
            r.raise_for_status()
            with target.open('xb') as f:
                for block in r.iter_content(1024 * 1024):
                    total += len(block)
                    if total > 256 * 1024**2:
                        raise ValueError('M02C media budget exceeded')
                    f.write(block)
            receipts.append({'title': entry['title'], 'requested_at': started, 'completed_at': now(),
                'url': url, 'final_url': r.url, 'status': r.status_code,
                'content_type': r.headers.get('Content-Type'), 'etag': r.headers.get('ETag'),
                'last_modified': r.headers.get('Last-Modified'), **info(target)})
        metrics = audio_info(target)
        sha = digest(target)
        exact = [a['asset_id'] for a in previous if a['sha256'] == sha or
                 (a['pcm_sha256'] == metrics['pcm_sha256'] and a['sample_rate'] == metrics['sample_rate']
                  and a['channels'] == metrics['channels'] and a['sample_width_bytes'] == metrics['sample_width_bytes'])]
        duration_matches = [a['asset_id'] for a in previous if abs(a['duration_s'] - metrics['duration_s']) < 0.25]
        asset = {'asset_id': str(uuid4()), 'kind': 'audio', 'relative_path': target.relative_to(ROOT).as_posix(),
                 'sha256': sha, 'source': {'source_id': 'PLAT-01', 'source_kind': 'public_voice_menu',
                 'page_url': observations['page_url'], 'resource_url': url,
                 'menu_locator': observations['menu_locator'] + ' / ' + entry['title']},
                 'source_version': '图鉴更新时间2025-12-29；编辑梦魣；与本地游戏构建映射unknown',
                 'collected_at': receipts[-1]['completed_at'], 'source_group_id': 'shorekeeper-profile-voice-1.3',
                 'labels': ['守岸人_menu_label', 'zh-CN_menu_label', entry['title'], 'audio_unlistened'],
                 'review_status': 'pcm_structure_checked_pending_listening', 'rights': rights()}
        acquired.append(asset)
        mapping.append({'asset_id': asset['asset_id'], 'source_kind': 'public_voice_menu',
            'source_url': url, 'resource_id': target.stem, 'relative_path': asset['relative_path'], 'sha256': sha,
            'container_sha256': None, 'internal_path': None, 'bank_id': None, 'stream_id': None,
            'title': entry['title'], 'menu_title_status': 'verified',
            'menu_character': '守岸人', 'menu_character_status': 'verified',
            'menu_language': '中文：唐雅菁', 'menu_language_status': 'verified',
            'audio_character_status': 'unknown', 'audio_language_status': 'unknown',
            'subtitle_status': 'unknown', 'subtitle': None, 'scene_route_status': 'unknown',
            'mapping_evidence': 'derived/menu_observations.json', 'audio_metrics': metrics,
            'exact_duplicate_of': exact, 'similar_duration_candidates': duration_matches,
            'possible_duplicate': True if duration_matches or exact else None,
            'duplicate_status': 'exact_duplicate' if exact else 'no_exact_hash_or_pcm_match_listening_pending',
            'duplicate_scope': [a['asset_id'] for a in previous],
            'source_group_id': asset['source_group_id'], 'counted_as_verified_independent_utterance': False,
            'review_status': 'pending_listening', 'training_admission': False,
            'note': '同源组保留；文件/PCM hash不相等不能排除重编码、裁剪或语义重复；未听审，不计独立新台词。'})
        previous.append({'asset_id': asset['asset_id'], 'sha256': sha, **metrics})
        print(entry['title'], target.stat().st_size, round(metrics['duration_s'], 6), flush=True)
    jsonl(BATCH / 'manifests/GAME-LOCAL.jsonl', containers)
    jsonl(BATCH / 'manifests/PLAT-01.jsonl', acquired)
    jsonl(BATCH / 'derived/audio_mapping.jsonl', mapping)
    save(REPORT / 'http_receipts.json', receipts)
    save(BATCH / 'derived/cross_batch_comparison.json', {'old_audio': previous[:5],
         'comparison_method': 'all original/PCM hashes, format, duration and menu title; new-to-ear identity unreviewed',
         'new_candidates': mapping, 'verified_independent_new_utterances': 0})
    base_path = ROOT / 'local/assets/derived/m02b-v1/transcripts.jsonl'
    base_lines = {json.loads(line)['transcript_id']: line for line in base_path.read_text(encoding='utf-8').splitlines()}
    supplements = []
    for s in feedback['transcript_supplements']:
        line = base_lines[s['base_transcript_id']]
        assert json.loads(line)['source_sha256'] == s['source_sha256']
        supplements.append({'transcript_id': 'M02C-T-' + s['candidate_id'].split('-')[-1],
            'supersedes_transcript_id': s['base_transcript_id'], 'feedback_id': feedback['feedback_id'],
            'feedback_ref': feedback_path.relative_to(ROOT).as_posix(), 'feedback_sha256': digest(feedback_path),
            'base_record_ref': {'path': base_path.relative_to(ROOT).as_posix(),
                                'record_sha256': hashlib.sha256(line.encode('utf-8')).hexdigest()},
            'asset_id': s['asset_id'], 'source_sha256': s['source_sha256'], 'title': s['title'],
            'text_verbatim': s['text_verbatim'], 'text_status': s['text_status'],
            'start_s': None, 'end_s': None, 'alignment_status': 'pending',
            'status': 'owner_text_supplement_not_time_aligned', 'training_admission': False})
    jsonl(BATCH / 'derived/transcript_supplements.jsonl', supplements)
    save(BATCH / 'batch.json', {'schema_version': '0.3', 'batch_id': BATCH.name, 'kind': 'incremental_asset_acquisition',
         'created_date': '2026-11-16', 'timezone': 'Asia/Shanghai', 'created_at': now(),
         'purpose': ['fixed_pak_feasibility_probe', 'bounded_public_menu_fallback', 'owner_text_ingestion'],
         'state': 'delivered_with_local_export_blocked_pending_coordinator_acceptance',
         'parent_refs': ['local/reports/M02A/local_inventory.json',
                         'local/assets/manifests/PLAT-01.jsonl',
                         'local/assets/derived/m02b-v1/transcripts.jsonl', feedback_path.relative_to(ROOT).as_posix()],
         'records': ['manifests/GAME-LOCAL.jsonl', 'manifests/PLAT-01.jsonl', 'derived/probe.json',
                     'derived/audio_mapping.jsonl', 'derived/transcript_supplements.jsonl', 'derived/review_index.md'],
         'counts': {'raw_files': 7, 'container_copies': 2, 'local_audio_exports': 0,
                    'public_menu_audio_candidates': len(acquired), 'verified_independent_new_utterances': 0,
                    'raw_bytes': total, 'transcript_supplements': 2, 'new_audio_listened': 0},
         'source_versions_ref': 'derived/input_snapshot.json',
         'tools_ref': 'local/reports/M02C/tool_versions.json',
         'dataset_release': None, 'resolved': ['fixed_input_hashes', 'protected_index_observed', '5 menu URL mappings', 'owner_text_refs'],
         'unresolved': ['protected_local_index', 'internal resource identities and dependencies',
                        'new_audio_listening', 'subtitle_alignment', 'independent_utterance_deduplication', 'material_rights']})
    parts = ['# M02C 新增语音复核入口', '',
             '来源：PLAT-01公开菜单补采；本地包导出0条。菜单身份已核，实际声音身份/语言与字幕仍待听核。',
             '已有5份总体反馈已保留，以下只列新增项。未判定为独立新台词，不进入训练。', '']
    players = []
    for a, m in zip(acquired, mapping):
        path = ROOT / a['relative_path']
        parts.append(f'- [{m["title"]}]({path.as_posix()}) — {m["audio_metrics"]["duration_s"]:.3f}秒；新增待复核。')
        players.append(f'<p>{html.escape(m["title"])}</p><audio controls preload="none" src="../raw/PLAT-01/{path.name}"></audio>')
    parts += ['', '两段用户全文：见 [transcript_supplements.jsonl](transcript_supplements.jsonl)。原文、前驱与反馈hash已核，句级时间null；未改旧草稿。',
              '', '本轮集中待核：新增5项的角色/语言、BGM/重叠、首尾完整性、失真、与旧5份及相互间的重复。未请求重审旧整包。',
              '字幕映射与句级时间另行核验；生日菜单使用【玩家名】的文字不证明音轨中具体称呼。',
              '本地索引需要针对该构建合法提供的适配解密材料或无保护定向导出；不扫描其他游戏包、不搜集密钥。']
    (BATCH / 'derived/review_index.md').write_text('\n'.join(parts) + '\n', encoding='utf-8')
    (BATCH / 'derived/review_index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>M02C 新增5项</title><body><h1>M02C 新增5项</h1><p>公开菜单补采；新增待听核，未用于训练。已有5份反馈保持原样。</p>' + ''.join(players) + '<p>用户补全文字见同目录 transcript_supplements.jsonl；时间仍未对齐。</p></body></html>', encoding='utf-8')


if __name__ == '__main__':
    main()
