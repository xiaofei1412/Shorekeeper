"""Validate this batch without changing historical evidence or approving audio by proxy."""
import copy
import hashlib
import importlib.util
import re
from pathlib import Path

from prepare_m02c import ROOT, BATCH, REPORT, digest, info, now, save, snapshot
from collect_m02c import audio_info, read, rows


def check_supplement(s, feedback, base_lines, old):
    original = next(t for t in feedback['transcript_supplements'] if t['asset_id'] == s['asset_id'])
    assert s['supersedes_transcript_id'] == original['base_transcript_id']
    line = base_lines[s['supersedes_transcript_id']]
    assert s['base_record_ref']['path'] == 'local/assets/derived/m02b-v1/transcripts.jsonl'
    assert s['base_record_ref']['record_sha256'] == hashlib.sha256(line.encode('utf-8')).hexdigest()
    assert s['source_sha256'] == original['source_sha256'] == old[s['asset_id']]['sha256']
    assert s['feedback_id'] == feedback['feedback_id']
    assert s['feedback_ref'] == 'local/assets/batches/B20261116-OWNER-01/owner_feedback.json'
    assert digest(ROOT / s['feedback_ref']) == s['feedback_sha256']
    assert s['text_verbatim'] == original['text_verbatim']
    assert s['title'] == original['title']
    assert s['start_s'] is None and s['end_s'] is None and s['alignment_status'] == 'pending'
    assert s['training_admission'] is False


def verify():
    baseline = read(REPORT / 'baseline_snapshot.json')
    current = snapshot()
    assert current == baseline['files'], 'Frozen file set/stat/hash/attributes changed'
    assert all(info(ROOT / p) == record for p, record in baseline['documents'].items())
    batch = read(BATCH / 'batch.json')
    assert batch['dataset_release'] is None
    assert batch['counts']['local_audio_exports'] == 0
    assert batch['counts']['public_menu_audio_candidates'] == 5
    assert batch['counts']['verified_independent_new_utterances'] == batch['counts']['new_audio_listened'] == 0
    assert all((BATCH / p).is_file() for p in batch['records'])
    manifests = [a for p in (BATCH / 'manifests').glob('*.jsonl') for a in rows(p)]
    assert len(manifests) == len({a['asset_id'] for a in manifests}) == batch['counts']['raw_files'] == 7
    actual_raw = {p.resolve() for p in (BATCH / 'raw').rglob('*') if p.is_file()}
    assert actual_raw == {(ROOT / a['relative_path']).resolve() for a in manifests}
    for a in manifests:
        assert {'asset_id', 'kind', 'relative_path', 'sha256', 'source', 'source_version',
                'collected_at', 'source_group_id', 'labels', 'review_status', 'rights'} <= a.keys()
        path = (ROOT / a['relative_path']).resolve()
        assert path.is_relative_to((BATCH / 'raw').resolve())
        assert digest(path) == a['sha256']
        assert set(a['rights']) == {'local_research', 'training', 'redistribution', 'commercial_use'}
        assert all(r['status'] == 'unknown' for r in a['rights'].values())
    raw_bytes = sum(p.stat().st_size for p in actual_raw)
    assert raw_bytes == batch['counts']['raw_bytes'] < 256 * 1024**2
    probe = read(BATCH / 'derived/probe.json')
    result = read(BATCH / 'derived/probe_tool_result.json')
    assert probe['tool_result'] == result
    assert result['index_status'] == 'blocked' and result['index_entry_count'] is None
    assert result['header']['EncryptedIndex'] is True
    assert result['exception_type'] == 'CUE4Parse.UE4.Exceptions.InvalidAesKeyException'
    assert result['exception_message'] == 'Reading encrypted data requires a valid aes key'
    assert result['export_status'] == result['decode_status'] == 'not_attempted'
    assert not (BATCH / 'derived/container_entries.jsonl').exists()
    assert probe['commands'][0]['exit_code'] == 0 and probe['commands'][1]['exit_code'] == 2
    for item in probe['inputs']:
        assert item['before'] == item['after_copy'] == item['after_probe'] == info(Path(item['before']['path']))
        assert item['before']['sha256'] == digest(Path(item['copy']['path']))
    input_snapshot = read(BATCH / 'derived/input_snapshot.json')
    for evidence in input_snapshot['version_evidence']:
        expected = {k: v for k, v in evidence.items() if k != 'relevant_lines'}
        assert info(Path(evidence['path'])) == expected
    old = {a['asset_id']: a for a in rows(ROOT / 'local/assets/manifests/PLAT-01.jsonl')}
    all_audio = [{**a, 'metrics': audio_info(ROOT / a['relative_path'])} for a in old.values()]
    menu = read(BATCH / 'derived/menu_observations.json')
    mappings = rows(BATCH / 'derived/audio_mapping.jsonl')
    new_audio = {a['asset_id']: a for a in manifests if a['kind'] == 'audio'}
    assert set(new_audio) == {m['asset_id'] for m in mappings} and len(mappings) == 5
    duration = 0
    for m, observation in zip(mappings, menu['entries']):
        a = new_audio[m['asset_id']]
        metrics = audio_info(ROOT / a['relative_path'])
        assert metrics == m['audio_metrics']
        assert m['sha256'] == a['sha256'] and m['relative_path'] == a['relative_path']
        assert m['source_url'] == observation['resource_url'] == a['source']['resource_url']
        assert m['title'] == observation['title'] and m['menu_title_status'] == 'verified'
        assert m['source_kind'] == a['source']['source_kind'] == 'public_voice_menu'
        assert m['source_url'] not in {v['source']['resource_url'] for v in old.values()}
        assert m['source_group_id'] == a['source_group_id'] == 'shorekeeper-profile-voice-1.3'
        assert m['audio_character_status'] == m['audio_language_status'] == m['subtitle_status'] == 'unknown'
        assert m['training_admission'] is False and m['counted_as_verified_independent_utterance'] is False
        assert m['review_status'] == 'pending_listening'
        assert all(m[k] is None for k in ['container_sha256', 'internal_path', 'bank_id', 'stream_id', 'subtitle'])
        exact = [p['asset_id'] for p in all_audio if p['sha256'] == a['sha256'] or p['metrics'] == metrics]
        assert exact == m['exact_duplicate_of'] == []
        assert m['duplicate_scope'] == [p['asset_id'] for p in all_audio]
        durations = [p['asset_id'] for p in all_audio if abs(p['metrics']['duration_s'] - metrics['duration_s']) < 0.25]
        assert durations == m['similar_duration_candidates']
        assert m['possible_duplicate'] is (True if durations else None)
        all_audio.append({**a, 'metrics': metrics})
        duration += metrics['duration_s']
    feedback = read(ROOT / 'local/assets/batches/B20261116-OWNER-01/owner_feedback.json')
    assert len(feedback['referenced_audio_assets']) == 5
    for ref in feedback['referenced_audio_assets']:
        assert ref['source_sha256'] == old[ref['asset_id']]['sha256'] == digest(ROOT / old[ref['asset_id']]['relative_path'])
    transcript_path = ROOT / 'local/assets/derived/m02b-v1/transcripts.jsonl'
    base_lines = {read_line['transcript_id']: line for line in transcript_path.read_text(encoding='utf-8').splitlines()
                  for read_line in [__import__('json').loads(line)]}
    supplements = rows(BATCH / 'derived/transcript_supplements.jsonl')
    assert len(supplements) == len({s['transcript_id'] for s in supplements}) == 2
    for s in supplements:
        check_supplement(s, feedback, base_lines, old)
    negatives = []
    for field, bad in [('text_verbatim', 'tampered'), ('source_sha256', '0' * 64),
                       ('supersedes_transcript_id', 'missing'), ('feedback_id', 'missing'), ('start_s', 0)]:
        invalid = copy.deepcopy(supplements[0])
        invalid[field] = bad
        try:
            check_supplement(invalid, feedback, base_lines, old)
        except (AssertionError, KeyError):
            negatives.append({'field': field, 'result': 'rejected_in_memory'})
        else:
            raise AssertionError('Invalid supplement accepted: ' + field)
    page = (BATCH / 'derived/review_index.html').read_text(encoding='utf-8')
    assert page.count('<audio controls') == 5 and '<script' not in page and '<iframe' not in page
    links = re.findall('src="([^"]+)"', page)
    assert all((BATCH / 'derived' / link).resolve().is_file() for link in links)
    for receipt in read(REPORT / 'http_receipts.json'):
        assert receipt['status'] == 200 and receipt['sha256'] == digest(Path(receipt['path']))
    provenance = read(REPORT / 'tool_runtime_provenance.json')
    for p in provenance['files']:
        assert digest(ROOT / p['relative_path']) == p['sha256']
    for download in read(REPORT / 'tool_downloads.json'):
        assert digest(Path(download['path'])) == download['sha256']
        if 'upstream_sha512' in download:
            assert digest(Path(download['path']), 'sha512') == download['upstream_sha512']
    spec = importlib.util.spec_from_file_location('frozen_m02b_verifier', ROOT / 'tools/assets_review/verify_m02b.py')
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    old_result = legacy.verify()  # read-only function; never call old main or refresh old reports.
    assert old_result['status'] == 'passed' and old_result['eval_cases'] == 20
    return {'checked_at': now(), 'status': 'passed', 'scope': 'integrity/references/PCM/probe boundaries; not listening or training approval',
        'frozen_files_unchanged': len(current), 'protected_documents_unchanged': len(baseline['documents']),
        'old_raw_assets': old_result['raw_assets'], 'old_raw_bytes': old_result['raw_bytes'],
        'legacy_m02b_verify_read_only': {'status': old_result['status'], 'frozen_files_unchanged': old_result['frozen_files_unchanged'],
                                       'review_targets': old_result['review_targets'], 'eval_cases': old_result['eval_cases']},
        'game_originals_unchanged': 2, 'work_copies_hash_matched': 2, 'container_index_status': 'blocked',
        'container_export_count': 0, 'public_menu_audio_candidates': 5, 'pcm_complete_read_count': 5,
        'audio_duration_s': duration, 'raw_files': 7, 'raw_bytes': raw_bytes,
        'new_audio_listened': 0, 'exact_hash_or_pcm_duplicates': 0, 'verified_independent_utterances': 0,
        'supplements_checked': 2, 'feedback_audio_hash_refs_checked': 5, 'negative_checks': negatives,
        'html_audio_controls': 5, 'html_playback_test': 'not_run', 'training_files_created': 0,
        'heldout_eval_unchanged': True, 'dataset_released': False,
        'dependency_packages': len(provenance['dependencies']),
        'batch_artifact_sha256': {p.relative_to(BATCH).as_posix(): digest(p) for p in sorted(BATCH.rglob('*')) if p.is_file()},
        'script_sha256': {p.name: digest(p) for p in sorted((ROOT / 'tools/assets_extract').iterdir()) if p.is_file()},
        'not_verified': ['audio character/language/quality', 'subtitle alignment', 'reencoded or semantic duplicates',
                         'internal resource identities/dependencies', 'sig cryptographic validation', 'training/redistribution rights']}


if __name__ == '__main__':
    result = verify()
    save(REPORT / 'verification.json', result)
    print(__import__('json').dumps({k: v for k, v in result.items() if not k.endswith('sha256')}, ensure_ascii=False, indent=2))
