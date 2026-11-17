**M05B1：常驻TTS服务与本地调用**

负责人：语音模块AI（本聊天负责交付，内部按文件分工协作）。状态：技术交付通过，待协调验收（2026-11-17）；新声音未直接听审，不以技术检查替代声音认可。

基线：M05A3提交23aaedfd3ce06848ad93377f11dd899d52d8c81a已协调技术验收并快进main；本次先核干净状态并fetch origin，将codex/m05a-voice工作树快进至用户派发的6127ac7316fbb60c81994207cfe98f1c349b49ad。总体方案v1.9、契约v0.5。依赖M01A与已接受的M05A系列，不依赖M00/M01B。

目标：输入任意合规新文字，经同一个常驻模型生成可读取WAV，并用最小CLI保存/可选播放。将固定文本实验变成可复用服务入口；不再逐句苛刻调参。

**范围与环境。**

- 本任务为唯一写入负责人，允许修改src/shorekeeper/{app.py,contracts.py,config.py,__main__.py}及新增src/shorekeeper/voice/、tools/voice_worker/、tools/speech_client/、tests/相关新增/受影响测试、configs/voice-profile.lock.json、configs/tts-service.example.json和本卡。确需复用可小改tools/voice_demo/的加载/溯源/预算/分组小函数，保持历史实验及测试可复现；不顺手重构旧校验器。
- 不修改AGENTS、总体方案、公共契约、其他任务卡、pyproject.toml/uv.lock或现有语音依赖锁。公共变化先提请求；本期用标准库/既有依赖，不给核心Python安装torch。不做ASR、情绪识别/控制、流式、打断、QQ、完整工作台/Electron、训练或exe打包。
- 先查Git并fetch，优先复用干净的codex/m05a-voice独立工作树，快进到最新派发提交；不要在主目录切分支。当前保留此开发分支用于连续语音工作，不删除远程分支/工作树。若新建工作树按create_worktree规则。
- 本机私有根仍E:\守岸人。语音Python3.10环境local/envs/voice-M05A、模型local/models/tts/M05A复用且核hash；核心Python3.12独立。实际可执行路径/源根通过本地启动配置注入，模板只含占位或相对路径，不写凭证/绝对私有路径。
- 私有服务配置放local/runtime/tts-config.json；服务WAV按local/runtime/tts/<session_id>/存放，报告放local/reports/M05B1/<run_id>/，缓存放local/cache/voice-M05B1/。原件、旧报告/试听页不覆盖，不复制进Git，不重新下载已通过hash的权重。

**实现切片。**

1. 固定一个可复用profile，版本shorekeeper-r1-v1。沿用R1原48k及manifest→OWNER全文→M02C-T-A05校验链，保留提示前缀、锁定模型/依赖、FP32与非流式。不套M05A2的中文谐音，不按题号挑seed/分组。通用完整句末分组保留原词组/小数/英文，原生停顿按已验证策略处理；不猜逗号语义、删波形静音或整体变速。统一种子策略并记录可复现seed，不自动重试筛选。
2. 独立语音worker一次加载模型，循环接请求；核心与worker经严格JSONL stdio通信。stdout只输出ready/result/error协议；导入/上游日志转私有stderr，普通日志不回显完整请求/参考/凭证。进程按参数列表启动、不经过shell，Windows隐藏子进程控制台；会话token留核心/客户端，worker不持有。每次生成前仍做完整语音LM预算，不截断。
3. 按docs/接口契约.md的v0.5实现POST speech、GET audio及status。保持默认未配置时原503行为，只有本地显式配置才启动worker；配置/依赖错误如实failed，不阻止进程健康接口可用。一个实际请求、零排队；API等待worker时不能阻塞health/status响应。 HTTP先监听，worker后台加载，使loading与health/status在加载中可观察，不能阻塞到ready才开放HTTP。
4. 成功WAV先在本会话临时路径写完，经完整帧/格式/有效信号检查后原子落盘，核心再登记随机UUID索引。核心只读取预定的本会话产物，不信任HTTP/worker返回的任意路径。仅已登记ID可经Bearer下载；未登记、半成品、旧会话文件即使存在也不开放。无需数据库/通用媒体平台，不自动删除历史产物。
5. 启动120秒/生成180秒超时，协议错乱或worker死亡废弃在途结果、终止本应用worker并tts=failed；迟到结果不登记、不改成成功，恢复需显式重启服务，不自动重发。断连不声称取消；退出只停止本应用子进程，不按通用进程名杀进程。故障用受控假worker加速验证，不必真等180秒。
6. 最小CLI读同一既有会话token文件，经Authorization提交文字/固定profile，取得素材ID后下载到本地，支持可选Windows winsound播放。默认自动读文件、不要求用户复制token；路径可用本地参数指定，token值不得作命令参数/URL/日志。返回“生成/保存成功”和“播放调用结束”各自实际状态，不声称已被人听见。暂不做浏览器凭证bootstrap或页面。 资产源根与后端令牌路径须区分，CLI读取正在服务的同一令牌文件，不能在另一个根或worker另建凭证。

**验收。**

- 先读M05A3协调接受与local/assets/batches/B20261117-OWNER-03/owner_feedback.json：允许语境/心情导致自然语速差异，不要求同文每次同节奏；重点是不当断词、漏读/重复、专名/数字错误和明显失真。情绪示例不固化为固定变速规则，本期不宣称已理解上下文或控制情绪。
- 核心默认无TTS配置时，现有health/status/speech校验/鉴权场景继续通过；保留M01A公开语义。开启后status真实呈现loading/ready/failed，product_ready仍false；不同合法新文本连续真实请求至少2次，证明同worker PID、模型只加载一次，返回200及真实可下载24kHz单声道PCM16。
- 真实CLI保存并可选播放新文本，保存hash与服务WAV一致；无token/错token的speech和audio均401，非法body/UUID为422，未知profile/素材ID404，忙时429，预算409，超时504，内部错误500，未配置/加载/failed503。检查错误结构及无原文/凭证/私有路径回显；下载只按本会话索引，路径穿越/未登记文件/上次会话ID不能取到音频。
- 用受控worker验证并发零队列、启动/生成超时、进程死亡、错请求关联、迟到结果、退出与重启；失败不返回200/假素材，不自动重发，旧ID重启后404。模拟与真实生成证据分开，核心CI无需下载模型/GPU。
- 跑受影响核心/API测试及原语音13＋4＋12项必要回归，明确跳过/失败项；历史音频/输入/报告hash保持不变。记录启动/模型加载/两次生成耗时、输出秒数、worker进程和显存/内存，不宣称全GPU/所有文本实时或主模型并发已测。
- 不重做已接受的逐句调参；若出现明显新失真或读错，记录具体文本/配置作为后续缺陷。技术交付后交协调助手验收，声音需要用户实际试听时提供直接可用新成果，不以ASR/指标替代听审。
- 代码/模板/脱敏交接提交并推送origin同名任务分支、核对SHA；不推main，服务配置/token、WAV、原始反馈/日志/报告与权重留本地。完成需真实HTTP/CLI场景通过，不以worker能加载代替服务可用。

**最终交接（2026-11-17）。**

使用干净的`C:\Users\lenovo\.codex\worktrees\m05a-voice\守岸人`，分支`codex/m05a-voice`，从23aaedf快进到派发基线`6127ac7316fbb60c81994207cfe98f1c349b49ad`。源根main未修改。交付提交为本卡所在提交；推送目标`origin/codex/m05a-voice`，远程SHA暂记待核验，最终回复实报，不循环提交自身SHA。保留语音工作树供后续验收，不推main。

修改范围：`src/shorekeeper/{app.py,contracts.py,__main__.py}`、新增`src/shorekeeper/voice/{service.py,windows_job.py,__init__.py}`、`tools/voice_worker/{run.py,test_worker.py}`、`tools/speech_client/{run.py,test_client.py}`、`tests/{test_voice_service.py,fake_voice_worker.py}`、`configs/{voice-profile.lock.json,tts-service.example.json}`及本卡。未改公共文档、核心/语音依赖锁、旧实验、源素材或旧报告。

**实现与固定profile。**

- `shorekeeper-r1-v1`锁定原48kHz单声道PCM24 R1、manifest→OWNER全文→M02C-T-A05链及原提示前缀；未把参考全文复制进仓库。启动重新核15个模型文件hash、manifest、运行锁、依赖包集合、Python3.10.6及CosyVoice/Matcha-TTS干净revision。模型与依赖版本沿用`voice-runtime.lock.json`，无下载/训练/重锁。
- 固定seed=20261117，每完整句重置同一seed，不按题号选seed、不重试筛选。复用已验证sentence_ranges与180ms边界补零策略；按。！？!?分组，保留全部输入字符、小数、词组和拉丁拼写。ASCII句点保留在组内，避免拆缩写/小数。没有谐音替换、逗号语义猜测、删静音、交叉淡化或全局变速。
- worker独立Python常驻一次加载，FP32、非流式、speed=1、text_frontend=false。每组前端实测完整文本、提示文本、提示speech token与特殊embedding，再加20倍生成预留和32安全量，与32768上下文比较；所有组预算通过后才开始生成，不截断。预算、seed、字符范围/hash、耗时及资源留私有stderr；普通日志不记录完整请求/参考，实际核过请求/参考全文及token均未出现。
- 严格JSONL：ready精确四个字段，request精确type/request_id/text/output_name，result精确type/request_id/seed；错误仅有request_id和受控code。stdout原管道单独保留，Python和原生fd1转私有stderr。token只由核心/客户端读取，不传worker。
- 后台loading不阻塞HTTP；单并发零队列，鉴权先于校验。120秒启动/180秒执行上限；预算409后继续ready，超时/死亡/错关联/协议错/内部错误置failed且不重发。失败后503，恢复须重启。HTTP断连继续占用生成槽，不声称取消。
- worker只写核心指定的本会话`UUID.wav.part`；核心检查RIFF总长度、24kHz/mono/PCM16、全帧与有效信号，原子改名后才登记随机UUID。audio只查本会话内存索引，Bearer覆盖整个前缀且no-store，不拼接输入路径，不扫描磁盘。重启丢弃索引，旧文件保留。
- Windows虚拟环境python.exe会再创建实际worker：使用挂起创建→加入本应用Job Object→恢复，ready核实际worker属于该Job，退出/故障终止所持Job，包含启动器先退出的情况；不按进程名杀其他服务。stdio写入及WAV检查在线程执行，堵塞管道不阻塞health。

**本机配置与命令。**

私有配置已建立`E:\守岸人\local\runtime\tts-config.json`，七项路径均相对该配置目录解析。模板可复制到同样的local/runtime位置；换机器需按实际目录修改。源根、模型和缓存均在E盘；资产源根与会话token文件明确分开，客户端不另建凭证。WAV在`local/runtime/tts/<session_id>/`，缓存`local/cache/voice-M05B1/`，最终证据`local/reports/M05B1/service-02/`；首轮与回归证据在`service-01/`。

以下在语音工作树运行；本轮真实HTTP用同一create_app启动loopback Uvicorn验证进程和真实子worker，客户端以独立进程调用，未用TestClient冒充网络。生产`__main__`入口的参数help已检查；两段最终产物均可直接读取，验证服务已正常关闭。

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = (Resolve-Path src).Path
$corePython = 'E:\守岸人\.venv\Scripts\python.exe'
& $corePython -m shorekeeper --tts-config 'E:\守岸人\local\runtime\tts-config.json' --token-file 'E:\守岸人\local\runtime\session.token'
# 服务ready后在另一终端调用；--play为可选的本机winsound播放。
& $corePython tools/speech_client/run.py --text '漂泊者，雨已经停了。出门时，记得带上那把伞。' --output 'E:\守岸人\local\reports\M05B1\manual-01.wav' --token-file 'E:\守岸人\local\runtime\session.token'
```

CLI默认自动读取本checkout/local/runtime/session.token；上述跨源根启动显式使用正在服务的同一文件。Bearer不在URL或命令值中。下载先完整校验再原子保存；生成/保存成功与可选播放调用结束分别报告，不声称有人听见。

**真实验证与资源（service-02最终代码）。**

- HTTP首次可用0.071秒，loading可观察；18.154秒ready，其中worker启动/校验/导入17.733秒、模型加载7.411秒。模型只加载一次，两个不同新文本由同worker PID11124处理；启动器PID另存real_http.json。
- HTTP新文本两完整句：生成10.941秒，输出242880帧/10.120秒；第二条CLI新文本保留2.5与B08原拼写，生成6.626秒，输出194880帧/8.120秒。均200、24kHz/mono/PCM16，下载no-store。CLI保存字节与服务下载完全相同。其文字仅为手工测试，不是官方台词或训练素材。
- 生成中health/status可响应、tts仍ready、product_ready=false；实际并发返回429。真实401（无/错token、speech/audio）、422（body/UUID）、404（profile/未知ID/未登记文件）、loading503与编码路径穿越拒绝通过。服务关闭后启动器退出，worker由owned Job关闭；没有后台遗留模型。
- 全部三组预算满足I+R+S≤C，C=32768、S=32；不声称全部文本可用或实时。原生句间静音保留，本次两句已有约551ms边界，未额外补零。
- RSS采样峰6,976,507,904字节，OS峰工作集7,513,407,488字节；CUDA allocated峰3,728,680,448、reserved峰4,081,057,792字节；整卡采样峰5888MiB（含其他进程）。实际llm/flow/hift在cuda:0，campplus/speech_tokenizer为CPU ONNX，未声称全GPU或主模型并发已测。
- 最终音频：`local/reports/M05B1/service-02/http-new-01.wav`、`cli-new-02.wav`。`real_http.json`、`worker_audit.json`、worker私有stderr、`http-run.log`、`log_privacy_check.json`记录实测。service-01保留首次真实运行，不覆盖旧成果；最终214个历史文件前后hash全部一致，见其preservation_before/after/check.json。

**自动化及受控故障验证。**

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
& 'E:\守岸人\.venv\Scripts\python.exe' -m pytest tests tools/voice_worker/test_worker.py tools/speech_client/test_client.py -q
& 'E:\守岸人\local\envs\voice-M05A\Scripts\python.exe' tools/voice_demo/test_inputs.py --source-root 'E:\守岸人' --derived-dir 'E:\守岸人\local\reports\M05B1\service-01\reference-regression' -v
& 'E:\守岸人\local\envs\voice-M05A\Scripts\python.exe' -m unittest discover -s tools/voice_demo -p test_experiment.py -v
& 'E:\守岸人\local\envs\voice-M05A\Scripts\python.exe' tools/voice_demo/test_m05a3.py --source-root 'E:\守岸人' --run-id focused-01 -v
git diff --check
```

最终pytest为98通过（核心41＋服务30＋worker12＋客户端15），原语音13＋4＋12为29通过，共127项，0失败/跳过。Starlette既有httpx弃用警告1项；不为消警改依赖锁。私有分项输出及final-pytest.txt在service-01。故障使用受控假worker与缩短内部超时，核心CI无需模型/GPU：覆盖启动/生成超时、预算409、内部500、进程死亡、错关联、重复/坏JSON/额外路径字段、错误/截断/静音WAV、堵塞管道、迟到包、断连仍占槽、关闭与重启旧ID404，以及Windows启动器先死时实际子进程也退出。模拟与真实模型证据分开，不声称真等180秒。两位协作审查者的问题已修复并复跑，未留阻塞项。

**限制、迁移与下一步。**

新声音未直接听审，未运行可选winsound实播；不认定专名/数字读法、漏读/重复、电音或声线已通过，不以输入文字/hash/技术信号检查替代用户判断。无ASR、情绪控制、流式、打断、身份/记忆/主模型接入；三个关联ID仅关联，无取消/幂等承诺。上游CPU ONNX provider、SDPA sliding-window及弃用警告保留在私有日志，未改第三方源码。

配置/数据迁移：仅新增显式本地TTS配置与内存会话索引，无数据库迁移。停用`--tts-config`即可回退not_configured/503，保留本地WAV/模型/缓存；不覆盖旧实验、反馈或依赖。接手协调助手核本分支、真实HTTP/CLI证据和两段新声音，再验收合并；人工声音反馈按新批次记录，不能回写旧OWNER结论。M05B2再做ASR/流式/取消，M04A再做工作台，真实聊天待M00/M01B。公共接口变更请求：无，按v0.5切片实现。
