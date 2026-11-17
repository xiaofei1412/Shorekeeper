"""M02C fixed input snapshot and portable tool acquisition; never scan game folders."""
import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BATCH = ROOT / 'local/assets/batches/B20261116-M02C-01'
REPORT = ROOT / 'local/reports/M02C'
TOOLS = ROOT / 'local/tools'
COMMIT = '4d2301a8eb129f593f1c76ff82612400801f1a94'
GAME = Path('E:/Wuthering Waves Game/Client/Saved/Resources/3.8.0')
PAK = GAME / 'RoleVoice/role_lang_shouanren_zh/Base/pakchunk296-WindowsNoEditor.pak'
SCOPES = ['local/assets/raw', 'local/assets/manifests', 'local/assets/derived/m02a-v1',
          'local/assets/derived/m02b-v1', 'local/reports/M02A', 'local/reports/M02B',
          'tools/assets_collect', 'tools/assets_review', 'local/assets/batches/B20261116-OWNER-01']
DOCS = ['README.md', 'AGENTS.md', 'docs/总体方案.md', 'docs/接口契约.md',
        'docs/素材来源清单.md', 'tasks/M02A-素材收集与首批证据.md', 'tasks/M02B-首批素材复核与角色规格.md']


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path, algorithm='sha256'):
    with path.open('rb') as f:
        return hashlib.file_digest(f, algorithm).hexdigest()


def info(path):
    before = path.stat()
    value = {'path': str(path), 'bytes': before.st_size, 'mtime_ns': before.st_mtime_ns,
             'attributes': before.st_file_attributes, 'sha256': digest(path)}
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), path
    return value


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def snapshot():
    return {p.relative_to(ROOT).as_posix(): info(p) for scope in SCOPES for p in sorted((ROOT / scope).rglob('*')) if p.is_file()}


def download(url, path, receipts, expected_sha512=None):
    assert not path.exists(), path
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(20, 60)) as r:
        r.raise_for_status()
        with path.open('xb') as f:
            for block in r.iter_content(1024 * 1024):
                f.write(block)
        receipt = {'url': url, 'final_url': r.url, 'http_status': r.status_code,
                   'retrieved_at': now(), **info(path)}
    if expected_sha512:
        receipt['sha512'] = digest(path, 'sha512')
        receipt['upstream_sha512'] = expected_sha512.lower()
        assert receipt['sha512'] == receipt['upstream_sha512']
    receipts.append(receipt)
    save(REPORT / 'tool_downloads.json', receipts)


def prepare():
    assert not BATCH.exists(), 'Do not refresh an existing batch baseline'
    BATCH.mkdir(parents=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    save(REPORT / 'baseline_snapshot.json', {'captured_at': now(), 'scope': SCOPES,
         'files': snapshot(), 'documents': {p: info(ROOT / p) for p in DOCS}})
    inputs = []
    for source in (PAK, PAK.with_suffix('.sig')):
        before = info(source)
        target = BATCH / 'raw/GAME-LOCAL' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        assert digest(target) == before['sha256']
        after = info(source)
        assert before == after
        inputs.append({'before': before, 'after_copy': after,
                       'copy': info(target)})
    install = Path('E:/Wuthering Waves Game')
    mounts = [GAME / 'Mount/MountResource.txt', GAME / 'Mount/MountLang_zh.txt',
              install / 'launcherDownloadConfig.json', install / 'launcherDownloadConfig/common.json',
              install / 'launcherDownloadConfig/uhd.json']
    versions = []
    for p in mounts:
        text = p.read_text(encoding='utf-8-sig')
        versions.append({**info(p), 'relevant_lines': [s for s in text.splitlines()
                         if '3.8.11' in s or 'shouanren' in s or '"version"' in s][:12]})
    save(BATCH / 'derived/input_snapshot.json', {'captured_at': now(), 'inputs': inputs,
         'version_evidence': versions, 'installation_bundle_version': '3.8.0',
         'installation_bundle_basis': 'launcherDownloadConfig.json bundles.UHD.version and common/uhd.json version; not a filename inference',
         'resource_patch_version': '3.8.11', 'scope': 'only explicit pak/sig copied; mount files read-only'})
    env = subprocess.run(['dotnet', '--info'], capture_output=True, text=True)
    save(REPORT / 'environment.json', {'checked_at': now(), 'dotnet_info': env.stdout,
         'python': __import__('sys').version, 'requests': requests.__version__,
         'process_observation': 'PowerShell process-name query found no Wuthering/launcher/KRSDK/Client-Win64 processes before copy; not an installation integrity proof'})
    print('snapshot and fixed copies created', flush=True)


def acquire_tools():
    receipts = []
    metadata_url = 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json'
    metadata_path = TOOLS / 'm02c-downloads/dotnet-10-releases.json'
    download(metadata_url, metadata_path, receipts)
    metadata = json.loads(metadata_path.read_text(encoding='utf-8-sig'))
    sdk = next(s for release in metadata['releases'] for s in release.get('sdks', [release['sdk']])
               if s['version'] == metadata['latest-sdk'])
    archive = next(f for f in sdk['files'] if f['rid'] == 'win-x64' and f['name'].endswith('.zip'))
    sdk_zip = TOOLS / 'm02c-downloads' / Path(archive['url']).name
    download(archive['url'], sdk_zip, receipts, archive['hash'])
    with zipfile.ZipFile(sdk_zip) as z:
        z.extractall(TOOLS / 'dotnet-m02c')
    source_zip = TOOLS / f'm02c-downloads/CUE4Parse-{COMMIT}.zip'
    download(f'https://codeload.github.com/FabianFG/CUE4Parse/zip/{COMMIT}', source_zip, receipts)
    with zipfile.ZipFile(source_zip) as z:
        z.extractall(TOOLS)
    save(REPORT / 'tool_versions.json', {'acquired_at': now(), 'cue4parse_commit': COMMIT,
         'cue4parse_source_version': '1.2.2', 'cue4parse_license': 'Apache-2.0',
         'dotnet_sdk': sdk['version'], 'dotnet_license': 'MIT; bundled third-party notices in SDK',
         'sdk_sha512_verified': True, 'tool_download_receipts': 'local/reports/M02C/tool_downloads.json',
         'native_build': 'CUE4PARSE_SKIP_NATIVE=true for index-only probe',
         'support_sources': [f'https://github.com/FabianFG/CUE4Parse/blob/{COMMIT}/{p}' for p in
         ['Directory.Build.props', 'LICENSE', 'CUE4Parse/UE4/Versions/EGame.cs', 'CUE4Parse/UE4/Pak/PakFileReader.cs']]})
    print('portable SDK and pinned CUE4Parse source ready', flush=True)


if __name__ == '__main__':
    import sys
    {'prepare': prepare, 'tools': acquire_tools}[sys.argv[1]]()
