# 航运 Agent 今日交付差距分析

检查时刻：2026-10-24T03:08:53Z

## 当前版本状态

- 项目：`/Users/jeremyyang/codes/湃乐多航运科技/笔试题AIAGENT/ship_invoice_agent`
- 分支：`main`
- Commit：`7299678e8188629133aff19f56591096ae89f2eb`
- 这是仓库唯一业务提交；10 月 22 日后没有新提交或业务代码修改。
- 工作区只有未跟踪的 `test-results/`；业务 tracked files 无 diff。
- 没有相关 pytest、Ruff、Uvicorn、Ship Invoice 或 OCR 容器正在运行。
- `.env` 不存在；没有发现真实模型输出或最终账单评估文件。

结论：10 月 22 日诊断的问题尚未修复。当前可以称为“结构完整、局部单测通过的 initial prototype”，还不能称为业务端到端验收完成。

## 今天重新执行的质量检查

### 通过

- `pytest -q -p no:cacheprovider`：13/13 通过。
- `ruff check src local_ocr tests scripts`：通过。
- 测试收集仍只有 13 个初始单测。

### 失败

- 根目录 `ruff check .` 失败：未跟踪的 `test-results/20261022T072845Z-local-ocr/harness/local_ocr_harness.py` 有两处 E501。业务源码本身通过，但当前工作区的 README 推荐命令不是绿色。

### 额外只读/临时探针

1. **recipient 冲突未拦截：失败。** 同 reference、charge、issuer、amount、currency，但 recipient 改成 `DIFFERENT RECIPIENT LLC`，Matcher 返回 `NO_CHANGE`，conflicts 为空。
2. **Agent 状态跨调用泄漏：失败。** 同一 Agent `apply=False` 连续处理同一文件：第一次 `CREATE`，第二次 `NO_CHANGE + DUPLICATE_IN_CURRENT_BATCH`，但 repository 仍为空。
3. **warning 不触发人工复核：失败。** `POSSIBLE_OCR_CHARACTER_CONFUSION` 已产生，但 `requires_review=False`，operation 仍为 `CREATE`。
4. **最小 JSON 落库与新 Agent 幂等：通过但仅探针。** `apply=True` 创建 1 条；新 Agent 再处理为 `NO_CHANGE`，记录数保持 1。
5. **瞬时 OCR 重试：通过但仅探针。** 两次 ConnectionError 后第三次成功；payload 与 `(10,300)` timeout 正确。
6. **OCR 4xx 策略：有缺陷。** 400 也被重试 3 次；应区分不可重试 4xx 与可重试 timeout/连接错误/429/5xx。

## 链路测试状态

| 链路 | 状态 | 当前证据 | 不能据此证明什么 |
|---|---|---|---|
| 本地文件 URL → 本地 OCR HTTP | 通过 | 4 个顶层源，7 个 RawDocument，6 次 OCR，全部 HTTP 200 | 不证明字段准确率或企业 OCR 响应兼容 |
| EML 正文和附件展开 | 部分通过 | 正文 + 3 个带 parent_source 的附件 | inline 营销图被错误送 OCR，尚未修复 |
| OCR 文本质量 | 部分通过 | PDF2 金额可读；HBL 有 O/0；PNG 有 6/G；营销图不应 OCR | 不证明模型能纠错 |
| LLM 抽取 | 仅 mock | 一个 FakeListChatModel happy path | 未用保存的真实 OCR fixture 测试说明性金额、O/0、低质量内容 |
| 费用标准化 | 仅单元 | 费用别名、币种大写、PP 标准化 | 未验证所有真实费用名和混合币种 |
| 金额/币种/合计 | 部分单元 | 只有 total mismatch 一例；现有 Golden 记录 PDF2 三条费用 | 没有真实 OCR→模型输出→合计评测；没有多币种、税、舍入边界 |
| 订单/账单匹配 | 部分单元且发现缺陷 | reference 策略的 CREATE/NO_CHANGE/UPDATE/currency conflict | BL、booking、container、ambiguous 未测；recipient 冲突当前漏判 |
| AR/AP 归属 | 仅单元 | 只测 accounting entity alias → AR | AP、缺主体、主体角色冲突、issuer/recipient 同时命中未测 |
| 批次去重/幂等 | 发现缺陷 | fresh Agent + apply 的简单重复探针通过 | 同 Agent 多次 process 错误共享 `_seen_fingerprints`；并发和崩溃恢复未测 |
| 来源证据与审计 | 部分实现 | file hash、source file、可选 page/text | page/text 非必填；parent_source 未进入最终 audit；证据无法阻止自动入账 |
| 人工复核拦截 | 部分实现且发现缺陷 | errors、低模型 confidence、AR/AP unresolved 会 review | OCR warning 不 review；模型自报 confidence，不是 OCR confidence |
| OCR 异常重试 | 部分实现、无正式测试 | 临时探针确认瞬时错误重试 | 4xx 无差别重试；Retry-After、429、失败审计未测 |
| JSON 落库 | 仅临时探针 | CREATE 后新 Agent 重复保持一条 | UPDATE、PENDING 不写、写失败恢复、并发锁未测 |
| 最终 BillOperation Golden | 未运行 | 只有 partial golden 和 evaluator 单测 | 没有代表真实 OCR 文档的完整最终 expected JSON |
| 外部 OCR / 真实 LLM | 未运行 | `.env` 不存在 | 无法声明真实模型准确率或生产可用 |

## 今天必须优先关注的测试结果

### P0-1 订单归属和交易对手冲突

已观察问题：Matcher 的 `_compare()` 检查 currency 和 issuer，但不检查 recipient；探针已证明错误 `NO_CHANGE`。

今天必须修改：recipient 不一致时必须 `PENDING_REVIEW`；补充 AP、AR、缺主体、主体角色冲突测试；补 BL/booking/container 匹配策略的正反例。

最小验收：

- reference 和费用相同但 recipient 不同 → `PENDING_REVIEW`，conflict.field=`recipient`。
- issuer 不同、currency 不同也分别 review。
- accounting entity 是 issuer/payee → AR，是 recipient → AP，无法唯一判断 → review。
- 所有匹配策略都不能跨交易对手错误更新。

### P0-2 费用行、金额、币种和合计

已观察问题：真实 PDF1 OCR 中 `$25/$1100/$150` 是条款金额；模型尚未运行，所以误抽只是已证实风险。PDF2 的三条费用和总额在 OCR 可读，但没有最终模型输出。

今天必须修改/补测：复用保存的 OCR JSON作为 fixture，使用确定性模型输出 mock 构造正确和错误两组输出；每条 charge 要有来源页和来源行；错误条款费用或合计不闭环必须 review。

最小验收：

- PDF1 只接受实际 `USD 245.0000`，拒绝 `$25/$1100/$150`。
- PDF2 接受 `65 + 1310.40 + 35 = 1410.40 USD`。
- 混合币种、缺币种、舍入超阈值、重复费用行分别有明确 expected operation。

### P0-3 来源证据与人工复核

已观察问题：`source_text/source_page` 可空；OCR warning 不触发 review；O/0 错误已在真实 HBL 发生。

今天必须修改：自动入账要求来源证据；identifier O/0 冲突、低质量 OCR、证据缺失必须 review。不得全局 O/0 替换；需要 EML/MBL/HBL 交叉佐证。

最小验收：

- 自动 CREATE/UPDATE 的每条 charge 可从对应页定位原文。
- 单独 HBL 的 `00LU...` → `IDENTIFIER_O0_AMBIGUOUS + PENDING_REVIEW`。
- EML 明文和附件共同支持 `OOLU...` 时可保留原观测与 canonical value 的审计链。
- warning-only 探针不能继续自动 CREATE。

### P0-4 重复处理与幂等

已观察问题：`_seen_fingerprints` 在 Agent 构造时初始化，跨多个 `process()` 调用保留；探针证明 repository 为空也会在第二次得到 NO_CHANGE。

今天必须修改：将 batch seen 集合限制在单次 `process()`，或者显式传入 batch context；持久幂等由 repository/matching 决定。

最小验收：

- 同 Agent、`apply=False`、两次独立 process 均根据 repository 得到 CREATE，不得以“当前批次重复”覆盖。
- 同一次 EML 批次的真实重复费用才标 `DUPLICATE_IN_CURRENT_BATCH`。
- `apply=True` 后新旧 Agent 重跑都 NO_CHANGE，JSON 记录数不增加。

### P0-5 落库结果

已验证仅有最简单 CREATE。今天至少补正式测试：CREATE、UPDATE、NO_CHANGE、PENDING_REVIEW 四种操作对临时 JSON 的最终内容。

最小验收：

- CREATE 增加一条；UPDATE 只改明确 changed fields；NO_CHANGE/PENDING 不改文件。
- 保存后重新载入 schema 有效。
- 模拟保存异常时原文件保持可解析，不留下被当作正式库的半文件。

### P1 异常重试

已观察问题：瞬时错误重试可用，但 400 也重试。

今天若有余量：只重试连接/timeout、429 和 5xx；普通 4xx 立即失败。补正式 mock 测试，不调用网络。

最小验收：timeout→重试后成功；400→1 次；429/503→按上限；最终异常包含 endpoint、尝试次数和可安全记录的状态，不泄露材料或凭据。

## 最小修改顺序

1. 先补失败测试：recipient conflict、warning→review、same-agent two process、apply matrix。
2. 最小修复 Matcher recipient 校验和 `_seen_fingerprints` 生命周期。
3. 增加自动入账证据门槛与 identifier ambiguity review。
4. 修 EML inline CID 过滤；顶层业务图片仍处理。
5. 用已保存 OCR JSON增加离线场景测试和完整 `BillOperation[]` expected JSON。
6. 收紧 OCR retry status 策略。
7. 处理 `ruff check .`：排除归档 `test-results` 或格式化归档 Harness，确保 README 命令再次绿色。
8. 最后才申请一次受控真实模型 Golden；未获授权时明确交付为 offline-validated initial，不宣称模型准确率。

## 可延后

- OCR DPI/PSM 大规模调参和图像增强。
- 保存完整 bounding box/token confidence 的 OCR 升级。
- Excel/Word 全格式矩阵、legacy `.doc`。
- 数据库迁移、并发锁、多 worker、Web UI。
- 更换 Agent 框架或大规模目录重构。

## 今天不要做

- 不要全局将 O 替换成 0 或反向替换。
- 不要用 HTTP 200、13 个单测或总体覆盖率代替业务验收。
- 不要在缺少 expected JSON 时直接调真实模型反复试 prompt。
- 不要先迁移数据库、重写框架或增加前端。
- 不要把 `test-results` 无选择地提交进业务仓库；先决定归档/忽略策略。

## 今日交付判断

当前 **不满足“业务端到端完成”条件**。主要阻塞是：订单归属 recipient 冲突漏判、跨调用幂等状态泄漏、warning 未阻断自动入账、真实 OCR fixture 尚未贯通到完整 expected BillOperation、正式落库矩阵未测。

如果今天完成上述 P0 修改和离线验收，可交付为：**本地 OCR + mock extraction + 确定性 reconciliation 的可复查 initial 版本**。

要声明完整业务可用，还需要经授权的一次真实模型 Golden，以及企业 OCR 响应契约验证；这两项目前都没有证据。

