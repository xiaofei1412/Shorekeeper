"""Read frozen M02A records and validate the M02B overlay; never rebuild M02A."""
import argparse
import copy
import hashlib
import json
import re
import sys
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'local/assets/derived/m02a-v1'
OUT = ROOT / 'local/assets/derived/m02b-v1'
REPORT = ROOT / 'local/reports/M02B'
SOURCE_IDS = {r['source_id'] for r in [json.loads(line) for line in
              (BASE / 'sources.jsonl').read_text(encoding='utf-8').splitlines() if line]}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_source_ref(ref, assets):
    require(isinstance(ref, dict) and ref.get('source_id') in SOURCE_IDS and ref.get('locator'), 'unlocated or unknown source')
    require(ref.get('url', '').startswith('https://'), 'source URL missing')
    if ref.get('asset_id'):
        require(ref['asset_id'] in assets, 'unknown referenced asset')
        asset = assets[ref['asset_id']]
        require(ref.get('sha256') == asset['sha256'], 'referenced asset hash mismatch')
        require(ref['source_id'] == asset['source']['source_id'], 'asset/source mismatch')


def base_records():
    rows = {}
    for filename, kind, key in [('evidence.jsonl', 'evidence', 'evidence_id'),
                                ('audio_candidates.jsonl', 'audio_candidate', 'candidate_id'),
                                ('scenes.jsonl', 'scene', 'scene_id')]:
        for raw in (BASE / filename).read_bytes().split(b'\n'):
            raw = raw.removesuffix(b'\r')
            if raw:
                record = json.loads(raw)
                rows[(kind, record[key])] = (record, sha(raw))
    return rows


def check_review(row, bases, assets):
    required = {'review_id', 'schema_version', 'target_kind', 'target_id',
                'base_record_sha256', 'asset_sha256', 'reviewer_kind', 'review_method',
                'reviewed_at', 'decision', 'checks', 'corrections', 'added_source_refs',
                'notes', 'supersedes_review_id'}
    require(required <= row.keys(), 'missing overlay fields')
    key = (row['target_kind'], row['target_id'])
    require(key in bases, 'unknown target')
    base, digest = bases[key]
    require(row['base_record_sha256'] == digest, 'base record hash mismatch')
    require(row['schema_version'] == '0.2', 'schema version mismatch')
    require(row['decision'] in {'accept_reference', 'needs_review', 'exclude'}, 'invalid decision')
    require(row['reviewer_kind'] in {'human', 'text_or_image_model', 'audio_capable_model', 'code_only'}, 'invalid reviewer')
    require(isinstance(row['review_method'], str) and row['review_method'].strip(), 'missing actual method')
    require(datetime.fromisoformat(row['reviewed_at']).tzinfo is not None, 'review time must have timezone')
    require(isinstance(row['corrections'], dict) and isinstance(row['checks'], dict), 'invalid checks/corrections')
    require(isinstance(row['added_source_refs'], list), 'invalid source references')
    require(set(row['corrections']) <= {'content'}, 'only content correction is supported; retain base identity/source/status')
    asset_id = base.get('asset_id') or base.get('context_ref', {}).get('asset_id') or row['checks'].get('reviewed_asset_id')
    if asset_id:
        require(row['asset_sha256'] == assets[asset_id]['sha256'], 'source asset hash mismatch')
    else:
        require(row['asset_sha256'] is None, 'unexpected asset hash')
    for ref in row['added_source_refs']:
        check_source_ref(ref, assets)
    if row['target_kind'] == 'audio_candidate':
        checks = row['checks']
        require({'direct_listening_performed', 'language', 'speaker_assessment', 'bgm',
                 'overlap', 'truncation', 'distortion', 'transcript_status'} <= checks.keys(), 'missing audio checks')
        direct = checks['direct_listening_performed']
        require(type(direct) is bool, 'listening flag must be boolean')
        if direct:
            require(row['reviewer_kind'] in {'human', 'audio_capable_model'}, 'reviewer cannot claim direct listening')
        else:
            require(row['decision'] != 'accept_reference', 'unlistened audio cannot be accepted')
            require(all(checks[k] in {'unknown', 'unreviewed'} for k in
                        ['language', 'speaker_assessment', 'bgm', 'overlap', 'truncation', 'distortion']),
                    'unlistened perceptual dimensions must remain unknown')
            require(checks['transcript_status'] != 'reviewed_by_listener', 'unheard transcript cannot be reviewed')
            require(checks.get('quality_candidate', False) is False, 'unheard audio cannot be quality candidate')


def check_transcript(row, assets, durations, audio_reviews):
    require({'transcript_id', 'asset_id', 'source_sha256', 'language', 'segments', 'status'} <= row.keys(), 'missing transcript fields')
    asset_id = row['asset_id']
    require(asset_id in durations and asset_id in audio_reviews, 'unknown transcript audio')
    require(row['source_sha256'] == assets[asset_id]['sha256'], 'transcript asset mismatch')
    require(row['status'] in {'draft_source_text', 'draft_asr', 'reviewed_by_listener'}, 'invalid transcript status')
    if row['status'] == 'reviewed_by_listener':
        require(audio_reviews[asset_id]['checks']['direct_listening_performed'], 'reviewed transcript lacks listening')
    else:
        require(row['language'] == 'unknown' or audio_reviews[asset_id]['checks']['direct_listening_performed'], 'unheard audio language must be unknown')
    require(row['segments'], 'empty draft')
    last_end = 0
    ids = []
    for segment in row['segments']:
        require({'segment_id', 'start_s', 'end_s', 'text', 'status', 'source_refs'} <= segment.keys(), 'missing segment fields')
        ids.append(segment['segment_id'])
        require(segment['status'] in {'alignment_pending', 'aligned', 'reviewed_by_listener'}, 'invalid segment status')
        if segment['status'] == 'reviewed_by_listener':
            require(row['status'] == 'reviewed_by_listener' and audio_reviews[asset_id]['checks']['direct_listening_performed'], 'reviewed segment lacks listening')
        start, end = segment['start_s'], segment['end_s']
        if start is None or end is None:
            require(start is None and end is None and segment['status'] == 'alignment_pending', 'unknown bounds must be null/pending')
        else:
            require(type(start) in {int, float} and type(end) in {int, float}, 'invalid time type')
            require(last_end <= start < end <= durations[asset_id], 'invalid audio time range')
            last_end = end
        require(segment['text'] and segment['source_refs'], 'missing text/source')
        for ref in segment['source_refs']:
            check_source_ref(ref, assets)
    require(len(set(ids)) == len(ids), 'duplicate segment id')


def negative_checks(bases, assets, reviews, transcripts, durations, audio_reviews):
    # Synthetic fixtures are independent of the real reviews' current listening state.
    audio = copy.deepcopy(next(r for r in reviews if r['target_kind'] == 'audio_candidate'))
    audio.update(reviewer_kind='code_only', decision='needs_review')
    audio['checks'].update(direct_listening_performed=False, language='unknown',
                           speaker_assessment='unreviewed', bgm='unreviewed', overlap='unreviewed',
                           truncation='unreviewed', distortion='unreviewed',
                           transcript_status='draft_source_text', quality_candidate=False)
    fixture_reviews = copy.deepcopy(audio_reviews)
    for row in fixture_reviews.values():
        row['checks']['direct_listening_performed'] = False
    transcript = copy.deepcopy(transcripts[0])
    transcript.update(status='draft_source_text', language='unknown')
    for segment in transcript['segments']:
        segment.update(start_s=None, end_s=None, status='alignment_pending')
    tests = []
    def reject(name, row, mutate, validate):
        bad = copy.deepcopy(row)
        mutate(bad)
        try:
            validate(bad)
        except ValueError:
            tests.append({'case': name, 'result': 'rejected'})
        else:
            raise ValueError('negative case was accepted: ' + name)
    check = lambda r: check_review(r, bases, assets)
    reject('unlistened_accept_reference', audio, lambda r: r.update(decision='accept_reference'), check)
    for kind in ['code_only', 'text_or_image_model']:
        def mutate(r, kind=kind):
            r.update(reviewer_kind=kind)
            r['checks']['direct_listening_performed'] = True
        reject(kind + '_direct_listening', audio, mutate, check)
    reject('missing_actual_method', audio, lambda r: r.update(review_method=''), check)
    reject('stale_base_hash', audio, lambda r: r.update(base_record_sha256='0' * 64), check)
    reject('stale_asset_hash', audio, lambda r: r.update(asset_sha256='0' * 64), check)
    reject('unheard_known_language', audio, lambda r: r['checks'].update(language='zh-CN'), check)
    reject('unheard_quality_candidate', audio, lambda r: r['checks'].update(quality_candidate=True), check)
    reject('correction_rewrites_identity', reviews[0], lambda r: r.update(corrections={'evidence_id': 'invented', 'source_type': 'official_publication', 'review_status': 'approved'}), check)
    tc = lambda r: check_transcript(r, assets, durations, fixture_reviews)
    reject('audio_time_out_of_range', transcript, lambda r: r['segments'][0].update(start_s=0, end_s=9999, status='aligned'), tc)
    reject('one_null_boundary', transcript, lambda r: r['segments'][0].update(start_s=0), tc)
    reject('unheard_reviewed_transcript', transcript, lambda r: r.update(status='reviewed_by_listener'), tc)
    reject('unheard_reviewed_segment', transcript, lambda r: r['segments'][0].update(start_s=0, end_s=1, status='reviewed_by_listener'), tc)
    reject('unknown_segment_status', transcript, lambda r: r['segments'][0].update(start_s=0, end_s=1, status='invented_status'), tc)
    reject('unknown_transcript_source_asset', transcript, lambda r: r['segments'][0]['source_refs'][0].update(asset_id='does-not-exist', sha256='0' * 64), tc)
    reject('wrong_transcript_source_hash', transcript, lambda r: r['segments'][0]['source_refs'][0].update(sha256='0' * 64), tc)
    human = copy.deepcopy(audio)
    human.update(reviewer_kind='human', decision='accept_reference',
                 review_method='Synthetic schema fixture only: human listens to the referenced file; this is not an actual review.')
    human['checks']['direct_listening_performed'] = True
    check(human)
    tests.append({'case': 'future_listener_state', 'result': 'accepted_schema_only',
                  'simulated_state_only': True, 'actual_listening': False})
    return tests


def verify():
    snapshot = json.loads((REPORT / 'baseline_snapshot.json').read_text(encoding='utf-8'))
    frozen_paths = {r['path'] for r in snapshot['files'] if any(r['path'].startswith(s + '/') for s in snapshot['scope'])}
    current_paths = {p.relative_to(ROOT).as_posix() for s in snapshot['scope'] for p in (ROOT / s).rglob('*') if p.is_file()}
    require(current_paths == frozen_paths, 'frozen directory file set changed')
    document_comparisons = []
    unchanged = 0
    for old in snapshot['files']:
        path = ROOT / old['path']
        current_hash = sha(path.read_bytes()) if path.exists() else None
        same = current_hash == old['sha256']
        unchanged += same
        if old['path'] in frozen_paths:
            require(same, 'frozen file changed: ' + old['path'])
            require((not bool(path.stat().st_mode & 0o200)) == old['read_only'], 'read-only flag changed')
        else:
            document_comparisons.append({'path': old['path'], 'start_sha256': old['sha256'],
                                         'current_sha256': current_hash, 'unchanged': same})
    assets = {r['asset_id']: r for f in (ROOT / 'local/assets/manifests').glob('*.jsonl') for r in read_jsonl(f)}
    durations, media = {}, []
    for asset in assets.values():
        path = ROOT / asset['relative_path']
        require(path.exists() and sha(path.read_bytes()) == asset['sha256'], 'manifest/raw mismatch')
        if asset['kind'] == 'audio':
            with wave.open(str(path), 'rb') as wav:
                params = wav.getparams()
                frames = wav.readframes(params.nframes)
                require(len(frames) == params.nframes * params.nchannels * params.sampwidth, 'incomplete WAV data')
                durations[asset['asset_id']] = params.nframes / params.framerate
                media.append({'asset_id': asset['asset_id'], 'duration_s': durations[asset['asset_id']],
                              'frames': params.nframes, 'sample_rate': params.framerate,
                              'channels': params.nchannels, 'sample_width_bytes': params.sampwidth,
                              'decoded_all_pcm_frames': True, 'direct_listening_performed': False})
    bases = base_records()
    reviews = read_jsonl(OUT / 'reviews.jsonl')
    expected = {k for k, (r, _) in bases.items() if k[0] != 'audio_candidate' or r.get('standalone_audio_acquired')}
    require(len(reviews) == len(expected) == 36, 'missing or duplicate target')
    require({(r['target_kind'], r['target_id']) for r in reviews} == expected, 'wrong target coverage')
    require(len({r['review_id'] for r in reviews}) == len(reviews), 'duplicate review id')
    for r in reviews:
        check_review(r, bases, assets)
        require(r['supersedes_review_id'] is None, 'initial M02B overlay has unexpected supersession')
    by_id = {r['target_id']: r for r in reviews}
    require(by_id['M02A-E08']['corrections'].get('content') == '胸口有向外延伸的晶体裂痕', 'E08 correction missing')
    for eid in ['M02A-E10', 'M02A-E11']:
        require(any('official_image_02.jpg' in r['locator'] for r in by_id[eid]['added_source_refs']), 'E10/E11 official support missing')
        require(by_id[eid]['checks']['redactions_preserved'] is True, 'redaction boundary missing')
    audio_reviews = {bases[('audio_candidate', r['target_id'])][0]['asset_id']: r for r in reviews if r['target_kind'] == 'audio_candidate'}
    transcripts = read_jsonl(OUT / 'transcripts.jsonl')
    require(len(transcripts) == 5 and {r['asset_id'] for r in transcripts} == set(audio_reviews), 'transcript coverage mismatch')
    require(len({r['transcript_id'] for r in transcripts}) == 5, 'duplicate transcript id')
    for row in transcripts:
        check_transcript(row, assets, durations, audio_reviews)
        require(row['status'] == audio_reviews[row['asset_id']]['checks']['transcript_status'], 'transcript/review status conflict')
    for r in reviews:
        if r['target_kind'] == 'scene':
            source = bases[('scene', r['target_id'])][0]
            require(r['checks']['sft_admission'] is False, 'unresolved scene entered SFT')
            require(r['checks']['node_digests'] == source['node_digests'], 'scene nodes changed')
    cases = read_jsonl(OUT / 'persona_eval_cases.jsonl')
    require(len(cases) == 20 and len({r['case_id'] for r in cases}) == 20, 'eval count/id mismatch')
    persona = (OUT / 'persona_draft.md').read_text(encoding='utf-8')
    rule_ids = set(re.findall(r'\bP\d{2}\b', persona))
    for row in cases:
        require({'case_id', 'scenario', 'input', 'rule_refs', 'evidence_refs', 'acceptable_behavior', 'failure_criteria', 'test_only'} <= row.keys(), 'missing eval fields')
        require(row['test_only'] is True and row['model_run_status'] == 'not_run', 'eval is not held out/unrun')
        require(set(row['rule_refs']) <= rule_ids, 'unknown persona rule')
        require(set(row['evidence_refs']) <= set(by_id) | {'M02A-PROJECT-DIRECTIVE'}, 'unknown eval evidence')
    negatives = negative_checks(bases, assets, reviews, transcripts, durations, audio_reviews)
    counts = {kind: dict(Counter(r['decision'] for r in reviews if r['target_kind'] == kind)) for kind in ['evidence', 'audio_candidate', 'scene']}
    return {'verified_at': datetime.now(timezone.utc).isoformat(), 'status': 'passed',
            'scope': 'integrity/schema/reference/state checks; not listening, licensing or model evaluation',
            'baseline_files_unchanged': unchanged, 'frozen_files_unchanged': len(frozen_paths),
            'raw_assets': len(assets), 'raw_bytes': sum((ROOT / r['relative_path']).stat().st_size for r in assets.values()),
            'review_targets': len(reviews), 'decisions': counts,
            'pending': {'direct_listening': sum(not r['checks']['direct_listening_performed'] for r in audio_reviews.values()),
                        'source_routing': 3, 'high_impact_text': 3, 'excluded_targets': sum(r['decision'] == 'exclude' for r in reviews)},
            'audio': media, 'total_audio_s': sum(durations.values()),
            'transcript_statuses': dict(Counter(r['status'] for r in transcripts)),
            'complete_source_text_drafts': sum(r['text_coverage'] == 'complete_source_text' for r in transcripts),
            'partial_source_text_drafts': sum(r['text_coverage'] == 'partial_source_excerpt' for r in transcripts),
            'listener_reviewed': sum(r['status'] == 'reviewed_by_listener' for r in transcripts),
            'quality_candidates': sum(bool(r['checks'].get('quality_candidate')) for r in audio_reviews.values()),
            'eval_cases': len(cases), 'eval_model_runs': 0, 'negative_checks': negatives,
            'python_version': sys.version, 'dependencies': 'Python standard library only',
            'protected_document_comparisons': document_comparisons,
            'authority_context_review_required': any(not d['unchanged'] for d in document_comparisons),
            'artifact_sha256': {p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file()},
            'verifier_sha256': sha(Path(__file__).read_bytes()),
            'report_input_sha256': {name: sha((REPORT / name).read_bytes()) for name in
                                    ['baseline_snapshot.json', 'source_checks.json', 'package_checks.json', 'preservation_checks.json']},
            'public_documents': 'compare current hashes to M02B start; later legitimate document upgrades are context drift requiring review, not M02A corruption; never refresh historical hashes'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--view', action='store_true', help='print effective evidence view in memory; validate first')
    args = parser.parse_args()
    try:
        result = verify()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, wave.Error) as error:
        result = {'status': 'failed', 'error': str(error), 'verified_at': datetime.now(timezone.utc).isoformat()}
        (REPORT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise SystemExit(str(error))
    (REPORT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.view:
        bases = base_records()
        for review in read_jsonl(OUT / 'reviews.jsonl'):
            if review['target_kind'] == 'evidence':
                base = copy.deepcopy(bases[('evidence', review['target_id'])][0])
                base.update(review['corrections'])
                print(json.dumps({'base_with_corrections': base, 'review': review}, ensure_ascii=False))
    else:
        print(json.dumps({k: result[k] for k in ['status', 'baseline_files_unchanged', 'review_targets', 'decisions', 'pending', 'quality_candidates', 'eval_cases']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
