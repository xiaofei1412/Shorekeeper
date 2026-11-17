"""为本轮16个原件生成有限索引；不保存整页剧情或训练格式。"""
import hashlib
import json
import wave
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'local/assets/derived/m02a-v1'
REPORT = ROOT / 'local/reports/M02A'
STAMP = datetime.now(timezone.utc).isoformat()
OUT.mkdir(parents=True, exist_ok=True)


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def lines(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


groups = {'OFF-01': 'shorekeeper-profile-voice-1.3', 'PLAT-01': 'shorekeeper-profile-voice-1.3',
          'COM-02': 'shorekeeper-profile-voice-1.3', 'COM-03': 'shorekeeper-profile-voice-1.3',
          'COM-04': 'shorekeeper-profile-voice-1.3', 'COM-01': 'shorekeeper-main-1.3-act8',
          'OFF-03': 'shorekeeper-main-1.3-act8', 'OFF-02': 'shorekeeper-pv-20240927',
          'OFF-04': 'blackshores-special-20240916', 'OFF-05': 'version-1.3-announcement'}
assets = []
for path in sorted((ROOT / 'local/assets/manifests').glob('*.jsonl')):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    for a in rows:
        a['source_group_id'] = groups[path.stem]
        a['review_status'] = {'image': 'visually_reviewed_reference_only',
                              'video': 'decoded_visual_sample_checked_audio_unlistened',
                              'audio': 'wav_structure_checked_unlistened'}[a['kind']]
        if path.stem == 'OFF-01' and '保存CDN实际返回版本' not in a['source_version']:
            a['source_version'] += '；保存CDN实际返回版本，不声称与发布者上传字节相同'
        assets.append(a)
    lines(path, rows)


def asset(sid, contains):
    return next(a for a in assets if a['source']['source_id'] == sid and contains in a['relative_path'])


receipts = json.loads((REPORT / 'http_receipts.json').read_text(encoding='utf-8'))
source_details = {
    'OFF-01': ('official_publication', '鸣潮官方版主 / TapTap user429475503', '2024-09-27', '5图已保存；图1/5同构图，长图顶部重复立绘'),
    'OFF-02': ('official_publication', '鸣潮 / B站mid1955897084', '2024-09-27T11:00:00+08:00', '中文P1视频及封面已取得；未实听'),
    'OFF-03': ('official_publication', '鸣潮 / B站mid1955897084', '2024-10-02T11:00:00+08:00', '中文P1视频及封面已取得；画面为男漂泊者；未实听'),
    'OFF-04': ('official_publication', '鸣潮 / B站mid1955897084', '2024-09-16（动态11:07；配套视频11:00，UTC+08:00）', '中文P1视频及黑海岸封面已取得；未实听'),
    'OFF-05': ('official_publication', '鸣潮 / B站', '2024-09-29T09:49:00+08:00', '正文全新剧情/潮汐任务/第一章第八幕已定位；未保存整页'),
    'PLAT-01': ('official_platform_edited', '库街区平台；最新编辑梦魣，有多名贡献者', '2025-12-29（页面更新时间）', '正常浏览器正文与中文语音菜单可用；5个公开WAV取得；平台编辑内容不等于官方逐字原稿'),
    'COM-01': ('community_transcription', 'The Hidden Gaming Lair', '游戏构建unknown；页面标题第一章第八幕', '937个顶层剧情节点；玩家文本路由不明，玩家名被替换；未保存全剧情'),
    'COM-02': ('community_transcription', 'wuthering.wiki', '游戏构建unknown；Last-Modified仅作网页时间', '档案和语音文字可读；HTML无audio；生日祝福替换为Rover'),
    'COM-03': ('community_transcription', 'Genshin Builds / WutheringWaves Builds', '游戏构建unknown', '档案语音文字可读；保留{PlayerName}/{TA}；页尾不获库洛背书'),
    'COM-04': ('community_edited', '鸣潮BWIKI玩家社区', 'revision7919；最后编辑2026-07-30 10:02', '语音/角色经历独立链接缺页，正文仍有故事；不据站点CC许可推断官方素材授权'),
}
sources = []
for r in receipts:
    sid = r['source_id']; kind, publisher, version, note = source_details[sid]
    sources.append({**r, 'source_type': kind, 'publisher': publisher, 'source_version': version,
                    'advertised_language': 'zh-CN', 'source_group_id': groups[sid],
                    'content_status': 'browser_rendered_checked' if sid == 'PLAT-01' else 'web_content_checked',
                    'acquired_asset_ids': [a['asset_id'] for a in assets if a['source']['source_id'] == sid],
                    'review_note': note, 'independence': '按来源组去重；镜像/同源转录不算独立设定证据'})
lines(OUT / 'sources.jsonl', sources)

# title/范围均为精确原文定位；content仅为概述，不代替原话。
data = [
    ('canon_fact', '生日为2月27日', 'COM-02', '基本信息/生日', '资料叙述者', 'not_applicable'),
    ('canon_fact', '出生及所属势力均为黑海岸', 'COM-02', '基本信息/出生、势力', '资料叙述者', 'not_applicable'),
    ('character_expression', '没有像人类那样亲历成长与衰老', 'COM-02', '守岸人的烦恼', '守岸人', '漂泊者'),
    ('character_expression', '希望借钢琴传达感情，邀请对方做首位听众', 'OFF-01', 'official_image_04.jpg/守岸人的喜好', '守岸人', '漂泊者'),
    ('character_expression', '喜欢漂泊者煮的暖浓汤，自己尝试复做', 'COM-02', '喜欢的食物', '守岸人', '漂泊者'),
    ('character_expression', '会避开腌制食品', 'COM-02', '讨厌的食物', '守岸人', '漂泊者'),
    ('character_expression', '守护众人已成为她自己的意志', 'OFF-01', 'official_image_04.jpg/守岸人的理想', '守岸人', '漂泊者'),
    ('canon_fact', '胸口晶体是由内延伸的能量裂痕', 'OFF-01', 'official_image_03.jpg/晶体状裂痕', '官方宣传叙述者', 'not_applicable'),
    ('character_expression', '承诺为漂泊者保守其过去', 'COM-02', '心声·三', '守岸人', '漂泊者'),
    ('canon_fact', '身体由高纯度回音能量晶体构成', 'COM-03', '共鸣评估报告', '档案叙述者（保留原黑条）', 'not_applicable'),
    ('canon_fact', '会用新能量晶体替换受损部分', 'COM-03', '超频诊断报告', '档案叙述者（保留原黑条）', 'not_applicable'),
    ('canon_fact', '周期时计为漂泊者赠礼', 'COM-03', '周期时计', '资料叙述者', 'not_applicable'),
    ('canon_fact', '唱片持续收录星球的声音', 'COM-03', '“我们”', '资料叙述者', 'not_applicable'),
    ('character_expression', '尊重椿对自身存在的选择', 'COM-03', '关于椿', '守岸人', '漂泊者（谈论椿）'),
    ('character_expression', '认可秋水的信息和交际能力', 'COM-03', '关于秋水', '守岸人', '漂泊者（谈论秋水）'),
    ('character_expression', '注意安可想象力中的生命力', 'COM-03', '关于安可', '守岸人', '漂泊者（谈论安可）'),
    ('character_expression', '自述与泰缇斯对情感的理解不同', 'COM-03', '关于泰缇斯', '守岸人', '漂泊者（谈论泰缇斯）'),
    ('character_expression', '安慰收容员后仍困惑于悲伤与情感', 'COM-01', '519-529', '守岸人', '漂泊者'),
    ('canon_fact', '自述未亲赴归墟港市，所复现故事来自漂泊者', 'COM-01', '672-674', '守岸人', '漂泊者'),
    ('character_expression', '出于自己的好奇询问漂泊者经历', 'COM-01', '682-685', '守岸人', '漂泊者'),
    ('character_expression', '记录饮品偏好并尝到苦味，回应减负建议', 'COM-01', '696-705', '守岸人', '漂泊者'),
    ('character_expression', '为保护漂泊者隐瞒自己的打算，并为冒险道歉', 'COM-01', '907-912', '守岸人', '漂泊者'),
    ('character_expression', '获得自由后仍愿参与黑海岸管理', 'COM-01', '930-937', '守岸人', '漂泊者'),
    ('canon_fact', '房间有放映机，她会认真观看电影', 'OFF-01', 'official_image_02.jpg/白昼逡巡', '官方宣传叙述者', 'not_applicable'),
    ('canon_fact', '尊重成员意愿并给予发挥所长的自由', 'OFF-01', 'official_image_02.jpg/简单即有效', '官方宣传叙述者', 'not_applicable'),
    ('canon_fact', '叙述梦见与等待的人在海岸外同行', 'OFF-01', 'official_image_02.jpg/清醒梦境', '官方宣传叙述者', 'not_applicable'),
    ('canon_fact', '身侧蝶形能量与群星回响有关', 'OFF-01', 'official_image_03.jpg/宇宙中的“蝴蝶”', '官方宣传叙述者', 'not_applicable'),
    ('character_expression', '以守岸人这个称呼表达因对方而有的意义与决心', 'PLAT-01', '角色语音/个性语音/中文/初奏', '守岸人', '漂泊者'),
]
evidence = []
url_by_id = {s['source_id']: s['url'] for s in sources}
for i, (claim, content, sid, locator, speaker, addressee) in enumerate(data, 1):
    row = dict(evidence_id=f'M02A-E{i:02}', claim_type=claim, content=content,
               source_ref={'source_id': sid, 'url': url_by_id[sid], 'locator': locator},
               source_type=source_details[sid][0], speaker=speaker, addressee=addressee,
               scene_id={'519-529': 'M02A-SC01', '696-705': 'M02A-SC02', '907-912': 'M02A-SC03'}.get(locator, 'profile_or_other'),
               rover_branch='unknown', source_group_id=groups[sid],
               context_ref={'url': url_by_id[sid], 'locator': locator},
               review_status='official_image_visually_checked' if sid == 'OFF-01' else
                             'platform_menu_checked_audio_unlistened' if sid == 'PLAT-01' else
                             'community_text_checked_game_unverified',
               note='概述不是逐字台词；canon_fact是内容类别，不表示已用游戏原件核准')
    if sid == 'OFF-01':
        a = asset(sid, locator.split('/')[0]); row['context_ref']['asset_id'] = a['asset_id']
    if sid == 'PLAT-01':
        row['context_ref']['asset_id'] = asset(sid, 'first_note')['asset_id']
    evidence.append(row)
lines(OUT / 'evidence.jsonl', evidence)
write(OUT / 'project_directive.json', {
    'claim_type': 'project_directive', 'content': '开发者用户对应唯一漂泊者；其他用户是朋友伙伴；关系不授予程序权限',
    'source_ref': 'E:/守岸人/AGENTS.md/先确认角色和任务', 'review_status': 'project_rule_checked',
    'note': '项目设定，非游戏原文，不计入28条角色证据；本轮未收录玩家解释为角色事实'})

# 只在内存检查网页文字，落盘段落摘要hash与定位；不复制完整场景。
pages = {}
for sid in ['COM-01', 'COM-02', 'COM-03']:
    r = requests.get(url_by_id[sid], headers={'User-Agent': 'Mozilla/5.0'}, timeout=40); r.raise_for_status()
    pages[sid] = BeautifulSoup(r.content.decode('utf-8'), 'html.parser')
story = pages['COM-01'].select('details ol')[0].find_all('li', recursive=False)
assert len(story) == 937
checks = []
for e in evidence:
    sid = e['source_ref']['source_id']; loc = e['source_ref']['locator']
    if sid not in pages:
        continue
    if sid == 'COM-01':
        start, end = map(int, loc.split('-')); block = '\n'.join(n.get_text(' ', strip=True) for n in story[start-1:end])
        selector = f'details ol（顶层直接子节点）/li[{start}..{end}]'
    elif sid == 'COM-02' and loc.startswith('基本信息'):
        block = '\n'.join(tr.get_text(' ', strip=True) for tr in pages[sid].select('tr') if any(k in tr.get_text() for k in ['生日', '出生', '势力']))
        selector = '页首基本信息表/对应tr'
    else:
        title = pages[sid].find(string=lambda x: x and x.strip() == loc)
        assert title is not None, loc
        p = title.parent
        while len(p.get_text(' ', strip=True)) < 60:
            p = p.parent
        block = p.get_text(' ', strip=True)
        assert len(block) < 2000, (loc, len(block))
        selector = ('span.skill > span.skillname（精确标题）/同级skilldescription' if sid == 'COM-02' else '精确标题/最近完整条目祖先')
    assert block
    checks.append({'evidence_id': e['evidence_id'], 'locator': loc, 'selector': selector,
                   'checked_at': STAMP, 'text_sha256': hashlib.sha256(block.encode('utf-8')).hexdigest(),
                   'normalization': "BeautifulSoup get_text(' ', strip=True); 范围节点以换行连接",
                   'text_length_chars': len(block), 'result': 'located', 'full_text_saved': False})
write(REPORT / 'text_locator_checks.json', checks)

scenes = []
for sid, start, end, context in [
    ('M02A-SC01', 519, 529, '收容员事件后辨认情感；前518有玩家问候，后530回到处理噬亡星'),
    ('M02A-SC02', 696, 705, '茶馆饮品互动；前695调查房间，后706继续推进任务'),
    ('M02A-SC03', 899, 915, '沙滩重逢解释过去和隐瞒；前898返回沙滩，后916等待'),
]:
    nodes = story[start-1:end]
    players = [li for n in nodes for ul in n.find_all('ul', recursive=False) for li in ul.find_all('li', recursive=False)]
    scenes.append(dict(scene_id=sid, source_ref=url_by_id['COM-01'], source_group_id=groups['COM-01'],
                       context_ref={'url': url_by_id['COM-01'], 'selector': 'details ol的顶层直接li子节点', 'start_entry': start, 'end_entry': end},
                       characters=['守岸人', '漂泊者显示文本'], context=context,
                       speaker_entries=len(nodes), displayed_player_text_count=len(players),
                       player_text_node_count=sum(bool(n.find('ul', recursive=False)) for n in nodes),
                       real_turn_count=None, routing='unresolved', rover_branch='unknown',
                       complete_scene_acquired=False,
                       node_digests=[{'entry': start+j, 'text_sha256': hashlib.sha256(n.get_text(' ', strip=True).encode('utf-8')).hexdigest()} for j,n in enumerate(nodes)],
                       review_status='continuous_web_locator_checked_game_routing_unverified',
                       note='显示条目数不是实玩往返轮数；箭头可能是分支或连续文本，不拼接为单一路由'))
lines(OUT / 'scenes.jsonl', scenes)
write(OUT / 'necessary_text_excerpt.json', {
    'source_ref': url_by_id['COM-01'], 'source_group_id': groups['COM-01'], 'collected_at': STAMP,
    'kind': 'selective_web_excerpt_not_complete_scene',
    'excerpts': [{'entry': 529, 'speaker': '漂泊者显示文本', 'text': '人的情感很复杂。'},
                 {'entry': 705, 'speaker': '守岸人', 'text': '你喜欢最重要。'},
                 {'entry': 915, 'speaker': '守岸人', 'text': '我回来了。'}],
    'note': '保留页面显示内容；没有恢复游戏占位符或宣称分支已知；共21个汉字及标点'})

titles = [('heart_1', '心声1'), ('likes', '守岸人的喜好'), ('worries', '守岸人的烦恼'),
          ('aspiration', '抱负和理想'), ('first_note', '初奏'), (None, '心声3'),
          (None, '喜欢的食物'), (None, '讨厌的食物'), (None, '关于黑海岸'), (None, '生日祝福')]
audio = []
wave_checks = []
for name, title in titles:
    a = asset('PLAT-01', name) if name else None
    duration = None
    if a:
        with wave.open(str(ROOT / a['relative_path']), 'rb') as w:
            duration = w.getnframes() / w.getframerate()
            wave_checks.append(dict(asset_id=a['asset_id'], duration_s=duration, channels=w.getnchannels(),
                                    sample_rate=w.getframerate(), sample_width_bytes=w.getsampwidth(), frames=w.getnframes()))
    audio.append(dict(candidate_id=f'M02A-A{len(audio)+1:02}', source_ref=url_by_id['PLAT-01'],
                      source_group_id=groups['PLAT-01'], title=title, locator='角色语音/个性语音/中文/'+title,
                      language='unknown', advertised_language='zh-CN', speaker='守岸人（菜单标注；未实听）',
                      scene='voice_menu', acquired=bool(a), standalone_audio_acquired=bool(a), listened=False,
                      asset_id=a['asset_id'] if a else None, absolute_path=str(ROOT / a['relative_path']) if a else None,
                      time_range_s=[0, duration] if duration else None,
                      noise='unreviewed', overlap='unreviewed', truncation='unreviewed', distortion='unreviewed',
                      mix_status='unreviewed', review_status='wav_structure_checked_unlistened' if a else 'menu_title_located_only'))
for source, span, context in [('OFF-02', [16, 27], 'PV开场叙述'), ('OFF-02', [112, 125], 'PV生命相关字幕段'),
                              ('OFF-02', [232, 245], 'PV末段守岸人近景'), ('OFF-03', [70, 90], '回响前段交流'),
                              ('OFF-03', [214, 236], '回响钢琴交流段')]:
    a = asset(source, '.mp4')
    audio.append(dict(candidate_id=f'M02A-A{len(audio)+1:02}', source_ref=a['source']['page_url'],
                      source_group_id=groups[source], title=context, locator=f'中文P1 / {span[0]}–{span[1]}秒（粗定位）',
                      language='unknown', advertised_language='zh-CN', speaker='守岸人候选/可能含漂泊者（未实听）',
                      scene='pv_performance' if source == 'OFF-02' else 'story_cutscene', acquired=True,
                      standalone_audio_acquired=False, listened=False, asset_id=a['asset_id'], absolute_path=str(ROOT / a['relative_path']),
                      time_range_s=span, noise='unreviewed', overlap='unreviewed', truncation='unreviewed', distortion='unreviewed',
                      mix_status='official_edited_video_audio_unreviewed', review_status='source_video_acquired_boundary_not_speech_aligned'))
lines(OUT / 'audio_candidates.jsonl', audio)
write(REPORT / 'wav_structure_checks.json', wave_checks)

descriptions = [
    ('OFF-01', 'official_image_01', '横版全身三分之四正面；平静；默认白浅蓝裙装/头纱；海岸与蝶群', 'default-promo-main-illustration'),
    ('OFF-01', 'official_image_02', '认识共鸣者长图；好奇/电影/成员自由/梦境及档案；顶部立绘重复', 'default-promo-profile-cards'),
    ('OFF-01', 'official_image_03', '初见共鸣者长图；半身与胸口晶体/肩颈细节；顶部立绘重复', 'default-promo-profile-cards'),
    ('OFF-01', 'official_image_04', '关于共鸣者长图；珍贵之物/理想/喜好/心声；Q版弹琴', 'default-promo-profile-cards'),
    ('OFF-01', 'official_image_05', '竖版海报；与图01为同一插画构图，不算另一角色设计', 'default-promo-main-illustration'),
    ('OFF-02', '_cover', '胸像三分之四角度；闭眼，双手交握胸前；平静', 'pv-suerfushi-cover'),
    ('OFF-03', '_cover', '面部近景；睁眼、嘴微张、眉尾略低；不据此断言心理情绪', 'echoes-cover'),
    ('OFF-04', '_cover', '黑海岸悬崖建筑远景；暗蓝天空与几何透明塔；无角色表情', 'blackshores-landscape-cover'),
]
art = []
for sid, name, description, family in descriptions:
    a = asset(sid, name)
    art.append(dict(reference_id=f'M02A-R{len(art)+1:02}', asset_id=a['asset_id'], source_ref=a['source'],
                    source_group_id=groups[sid], acquired=True, absolute_path=str(ROOT / a['relative_path']),
                    appearance_version='2024年1.3默认形象/相关黑海岸宣传', description=description,
                    duplicate_visual_family=family, use='reference_only', is_riggable_model=False,
                    redistribution_rights='unknown', review_status='visually_reviewed'))
lines(OUT / 'art_references.jsonl', art)
print(json.dumps(dict(sources=len(sources), evidence=len(evidence), scenes=len(scenes), audio_candidates=len(audio),
                      standalone_wav=len(wave_checks), art_references=len(art), text_locators_checked=len(checks))))
