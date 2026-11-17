"""Reproduce the index-only probe using pinned local tools and the batch copy."""
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from prepare_m02c import ROOT, BATCH, REPORT, TOOLS, COMMIT, digest, info, now, save
from collect_m02c import read


def main():
    env = dict(os.environ, DOTNET_CLI_HOME=str(TOOLS / 'dotnet-home-m02c'),
               NUGET_PACKAGES=str(TOOLS / 'nuget-m02c'), DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
               DOTNET_CLI_TELEMETRY_OPTOUT='1')
    dotnet = TOOLS / 'dotnet-m02c/dotnet.exe'
    commands = [[str(dotnet), 'build', 'tools/assets_extract/M02CProbe.csproj', '-c', 'Release',
                 '-p:CUE4PARSE_SKIP_NATIVE=true', '-p:NuGetAudit=false', '--verbosity', 'quiet'],
                [str(dotnet), 'local/tools/m02c-probe/bin/Release/net10.0/M02CProbe.dll']]
    outcomes = []
    for command, log in zip(commands, ['probe_build_final.log', 'probe_run.log']):
        with (REPORT / log).open('w', encoding='utf-8') as f:
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)
        outcomes.append({'argv': command, 'exit_code': process.returncode,
                         'log': 'local/reports/M02C/' + log, 'log_sha256': digest(REPORT / log)})
        assert process.returncode in ([0] if len(outcomes) == 1 else [0, 2])
    result = read(BATCH / 'derived/probe_tool_result.json')
    inputs = read(BATCH / 'derived/input_snapshot.json')
    for inp in inputs['inputs']:
        inp['after_probe'] = info(Path(inp['before']['path']))
        assert inp['before'] == inp['after_probe']
        assert inp['before']['sha256'] == digest(Path(inp['copy']['path']))
    packages = read(TOOLS / 'm02c-probe/obj/project.assets.json')
    dependencies = []
    for key, value in packages['libraries'].items():
        if value['type'] != 'package':
            continue
        directory = TOOLS / 'nuget-m02c' / value['path']
        nuspec = next(directory.glob('*.nuspec'))
        xml = ET.parse(nuspec)
        license_node = next((e for e in xml.iter() if e.tag.split('}')[-1] == 'license'), None)
        dependencies.append({'package': key, 'source': 'https://api.nuget.org/v3/index.json',
            'resolved_sha512': value.get('sha512'),
            'license_declared': license_node.text if license_node is not None else None,
            'nuspec_path': nuspec.relative_to(ROOT).as_posix(), 'nuspec_sha256': digest(nuspec)})
    provenance_files = [TOOLS / 'dotnet-m02c/dotnet.exe',
        TOOLS / 'm02c-probe/bin/Release/net10.0/M02CProbe.dll',
        TOOLS / 'm02c-probe/bin/Release/net10.0/CUE4Parse.dll',
        TOOLS / 'm02c-probe/obj/project.assets.json',
        TOOLS / f'CUE4Parse-{COMMIT}/Directory.Packages.props',
        TOOLS / f'CUE4Parse-{COMMIT}/Directory.Build.props',
        TOOLS / f'CUE4Parse-{COMMIT}/LICENSE']
    save(REPORT / 'tool_runtime_provenance.json', {'checked_at': now(), 'commands': outcomes,
         'cue4parse_commit': COMMIT, 'dependencies': dependencies,
         'files': [{**info(p), 'relative_path': p.relative_to(ROOT).as_posix()} for p in provenance_files],
         'scripts': {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted((ROOT / 'tools/assets_extract').iterdir()) if p.is_file()},
         'license_note': 'Tool licenses and dependency declarations do not establish rights for game or public audio assets.',
         'native_decoder_installed_for_export': False})
    save(BATCH / 'derived/probe.json', {'checked_at': now(), 'tool_result': result,
         'inputs': inputs['inputs'], 'version_evidence': inputs['version_evidence'],
         'commands': outcomes, 'tool_versions_ref': 'local/reports/M02C/tool_versions.json',
         'tool_runtime_provenance_ref': 'local/reports/M02C/tool_runtime_provenance.json',
         'internal_entries': None if result['index_status'] == 'blocked' else 'derived/container_entries.jsonl',
         'local_audio_exports': 0, 'local_decode_attempted': False,
         'next_minimum_requirement': '合法提供、适配本构建的索引解密材料，或经授权的无保护定向导出。当前未提供；不搜集密钥、不转储/注入进程。',
         'dependencies_from_internal_index': None,
         'dependency_status': 'unknown_due_to_protected_index',
         'notes': ['header lists None/Oodle as available container compression methods; actual entries/codecs unknown',
                   'sig copied and hashed, cryptographic signature not verified',
                   'index count unknown, no empty table emitted as successful index',
                   'public menu fallback is independent of local-container extraction']})
    print(result['index_status'], result.get('exception_type'), 'source and copy hashes unchanged')


if __name__ == '__main__':
    main()
