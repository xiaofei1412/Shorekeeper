**M05B1：常驻TTS服务与本地调用**

负责人：未认领，由用户派给语音模块AI。状态：可派发（2026-11-17）；这是设计任务卡，服务尚未实现。

基线：M05A3提交23aaedfd3ce06848ad93377f11dd899d52d8c81a已协调技术验收并快进main；正式开工使用包含本卡与契约v0.5的最新协调提交，记录实际ref。总体方案v1.9。依赖M01A与已接受的M05A系列，不依赖M00/M01B。

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

**最终交接（在本卡填写）。**

- 负责人、实际分支/基线/提交、修改路径：待填写。
- profile/环境/启动与CLI命令、配置位置、私有证据：待填写。
- 真实验证/故障模拟的命令结果与限制、未测项：待填写。
- Git远程分支/核验SHA/推送结果：待填写；最终回复实报，不循环提交自身SHA。
- 配置/数据迁移：新增本地TTS配置与本会话音频索引；不建业务数据库。回退移除显式TTS启动配置即恢复原503，保留本地音频；历史实验和旧锁不覆盖。
- 已知限制/接手者下一步：待填写；M05B2再接ASR/流式/取消，M04A再做语音/状态工作台，真实聊天待M00/M01B。
- 公共接口变更请求：无；若需超出v0.5，先按AGENTS提变更请求。
