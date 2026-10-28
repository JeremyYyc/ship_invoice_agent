# Ship Invoice Agent

Python + LangChain 航运智能账单助手，支持从 EML、PDF、图片、Excel 和 Word 中提取费用，使用
JSON 历史账单完成新增、更新、去重、冲突检测和 AR/AP 判断。

## 首次迭代的边界

- PDF/图片调用题目给定的 OCR HTTP 请求契约。
- 本地提供一个使用 Tesseract 的黑盒 OCR 服务。
- OCR Adapter 将已支持的响应转为带页码的文本；未知结构回退为清理后的 JSON。
- 此方案要求企业 OCR JSON 中包含可读的文本/表格；异步任务、加密数据或二次查询需要后续适配。
- 默认只读 JSON 账单库；只有显式 `--apply` 才写回。
- 没有 Web 界面或数据库。

## 架构

```text
EML / PDF / Image / Excel / Word
                  |
          parser or OCR API
                  |
             RawDocument
                  |
       Strict JSON Schema extraction
                  |
   documents[] + verified evidence
                  |
 normalization / validation / matching
                  |
 CREATE / UPDATE / NO_CHANGE / PENDING_REVIEW
                  |
   validate/serialize -> apply -> JSON array
```

模型负责语义抽取；费用标准化、数字校验、历史匹配、冲突判定和写回都是确定性 Python 逻辑。

## 环境要求

- Python 3.11+
- Docker（推荐，用于本地 OCR）
- 一个可用的聊天模型 API

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

在 `.env` 中配置模型。阿里云百炼示例：

```dotenv
LLM_PROTOCOL=openai_compatible
LLM_PROVIDER=qwen
LLM_MODEL_NAME=qwen3.8-flash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-your-key
```

### 更换模型

对 OpenAI-compatible 服务，只需替换：

```dotenv
LLM_BASE_URL=https://company-model.example.com/v1
LLM_API_KEY=company-key
LLM_MODEL_NAME=company-model
```

对原生 LangChain Provider：

```dotenv
LLM_PROTOCOL=langchain_provider
LLM_PROVIDER=anthropic
LLM_MODEL_NAME=your-model-id
```

并安装对应 extra，如 `pip install -e ".[anthropic]"`。原生 Provider 的凭证按其官方 LangChain
集成文档配置。

默认 `LLM_STRUCTURED_OUTPUT_MODE=native`，显式调用 `json_schema + strict=True`。
`prompt` 是必须手动选择的兼容模式，也执行同一套本地 JSON Schema 和证据校验；
接口不支持严格 Schema 或返回配置错误时直接失败，不会静默降级。
默认模型为 `qwen3.8-flash`，替换模型后运行 `check-model` 检查实际契约兼容性。

模型调用默认采用 115 秒单次超时、每份 `RawDocument` 235 秒总预算以及一次应用层重试。
Provider SDK 的内部重试被关闭，避免重试次数相乘。抽取或 Schema 校验耗尽重试预算时，
当前文档输出 `PENDING_REVIEW`，并继续处理同一输入中的其他附件。拒绝或输出截断直接转复核；
不可重试的 4xx 配置错误停止任务。三个参数可通过
`LLM_TIMEOUT_SECONDS`、`LLM_TOTAL_TIME_BUDGET_SECONDS` 和 `LLM_MAX_RETRIES` 调整。

Qwen 的思考模式默认显式关闭：`LLM_ENABLE_THINKING=false`。在 Qwen 的
OpenAI-compatible 调用中，该值通过 `extra_body.enable_thinking` 发送；其他模型厂商不会收到
这个非标准参数。`ship-invoice-agent check-model` 的输出会显示当前 `enable_thinking` 配置。

## 启动本地 OCR

```bash
mkdir -p .runtime/files
docker compose up --build -d
```

由于 OCR 在 Docker 网络中访问文件服务，使用 Compose 时在 `.env` 中设置：

```dotenv
OCR_API_URL=http://127.0.0.1:8001/process
LOCAL_FILE_BASE_URL=http://file-server:8081
```

检查：

```bash
curl http://127.0.0.1:8001/health
```

如本机 `8001` 端口已被占用，可设置 `OCR_HOST_PORT=18001`，并将
`OCR_API_URL` 同步改为 `http://127.0.0.1:18001/process`。

本地 OCR 请求和题目一致：

```json
{
  "file": "http://file-server:8081/invoice.pdf",
  "task": "orderChange",
  "callBack": "",
  "taskType": ""
}
```

本地 OCR 响应进入 LLM 前会经过 `LocalOCRAdapter`：优先保留带 `[PAGE n]`
标记的 `pages[].text`；页面文本缺失时使用该页 `lines`，全部页面内容缺失时再回退到
`full_text`。因此不会同时发送 `pages[].text`、`pages[].lines` 和 `full_text` 三份重复内容。
无法识别的企业 OCR Schema 暂时回退为删除二进制字段后的原始 JSON，后续可增加对应 Adapter。

费用名称标准化采用原始证据优先策略。`original_charge_item` 或 `source_text` 中命中的
`data/charge_aliases.json` 别名优先于模型生成的 `charge_item`。如果两者映射到不同标准费用，
最终使用确定性别名映射，并记录 `MODEL_CHARGE_NAME_CONFLICT` 与
`MODEL_CHARGE_NAME_OVERRIDDEN`。默认 `CHARGE_ALIAS_CONFLICT_POLICY=review`，冲突记录进入
`PENDING_REVIEW`；别名表本身存在一对多冲突时，应用启动即失败。

企业环境中只替换 `OCR_API_URL` 和每个文件的 `file_url`。

## 使用

先使用合成数据检查模型的严格抽取契约和原文证据能力：

```bash
ship-invoice-agent check-model
```

处理 PDF：

```bash
ship-invoice-agent process \
  --input "tests/fixtures/documents/PDF 示例账单2.pdf" \
  --accounting-entity "Pilot Aiship Tech co., Ltd."
```

显式的文件 URL：

```bash
ship-invoice-agent process \
  --input "tests/fixtures/documents/PDF示例账单1.pdf" \
  --file-url "http://file-server:8081/PDF%E7%A4%BA%E4%BE%8B%E8%B4%A6%E5%8D%951.pdf" \
  --accounting-entity "Neptune Shipping Limited"
```

处理 EML 时会读取邮件头、正文，并递归处理附件：

```bash
ship-invoice-agent process \
  --input tests/fixtures/documents/sample-pre-alert.eml \
  --accounting-entity "Pilot Aiship Tech co., Ltd."
```

stdout 只有最终 JSON，日志写到 stderr。

## 输出

顶层始终是 JSON 数组，每条费用一条记录：

```json
[
  {
    "operation": "CREATE",
    "document_info": {
      "document_type": "Debit Note",
      "reference_no": "DC108478",
      "document_date": "2026-10-24"
    },
    "transaction_party": {
      "issuer": "Pilot Aiship Tech co., Ltd.",
      "recipient": "AEBO TCO IMPORT LLC"
    },
    "charge_details": {
      "charge_item": "Handling Charge",
      "amount": 65.0,
      "currency": "USD",
      "payment_term": null
    },
    "financial_attributes": {
      "accounting_entity": "Pilot Aiship Tech co., Ltd.",
      "posting_type": "AR"
    },
    "audit_trail": {
      "ai_reasoning": [
        "未找到符合已配置匹配策略的历史账单",
        "记账主体为出票方或收款方，判定为应收"
      ]
    }
  }
]
```

默认 `OUTPUT_SCHEMA_MODE=assignment`，stdout 严格只包含题目规定的字段；`audit_trail`
只包含 `ai_reasoning`。内部的来源文件、置信度、warning、字段变化和冲突仍保留在
`BillOperation` 中，其中需要对外展示的诊断会合并进标准输出的 `ai_reasoning`。
标准输出的推理和诊断文案统一使用中文；内部模式仍保留原始推理、warning code
与字段级诊断，不改变业务判定。
缺失字段使用 `null`，金额为 JSON number。

仅在本地调试或保存内部诊断时可设置 `OUTPUT_SCHEMA_MODE=internal`；交付和验收环境必须保持
`assignment`。

## 严格抽取契约和写回流程

运行时契约在 `src/ship_invoice_agent/contracts/`；`schemas/draft/` 保留可读的同版本副本，
回归测试检查两者一致。当前抽取契约为 `extraction-v2.schema.json`，最终输出仍为
`assignment-output-v1.schema.json`。所有对象禁止额外字段，声明键必须出现。
抽取事实采用 `{"value": ..., "source_line_ids": [...]}`：非空值至少引用一行，未知值必须是
`{"value": null, "source_line_ids": []}`；箱号等集合的每个元素也携带引用，空集合为 `[]`。
编号值保持字符串，金额值必须为有限 JSON 数字，解析时保留 Decimal 精度。
金额在匹配、内部模型、JSON 输出和账单存储中均保留 Decimal 精度，不通过 float 中转。

- LLM 返回 `documents[]`，每份逻辑单据包含原始身份、交易角色、费用、分币种汇总、证据和显式变更请求。
- 程序为输入文本建立稳定行 ID，保留可识别的页码、sheet、表格和文本行位置。
  当前原生 Office 解析仍是展平文本，证据不宣称具备原始单元格坐标。
- 原生请求把可引用行 ID 限定为本次输入的枚举，通过 `anyOf` 强制非空值与非空引用一起生成。
  本地也执行同一完整性与行 ID 校验，再解包为原有内部字段和证据路径，继续执行 A07 原文支持核验。
  `document_type` 与 `document_type_raw` 各自必须有引用；不能仅靠提示词要求，也不依赖同行补证。
  v1 Schema 和历史报告保留用于追溯；生产抽取不静默接受旧版平铺值与独立 `evidence[]`。
- 标准费用名、AR/AP、历史匹配、操作类型和中文审计由代码生成。新契约不使用模型自报置信度。
  已应用记录的证据和处理时间保存在历史账单 `audit_history`；待复核记录的完整诊断可用
  `OUTPUT_SCHEMA_MODE=internal` 保存，题目标准输出只保留规定的审计字段。
- 缺少证据、未知标准费用、缺关键入账字段、身份混淆或角色冲突时转复核。
  OCA 等尚未进入词表的费用会因此进入复核。`ADD'L DETENTION FREE TIME CHARGE` 已补入别名。
- 新契约下，差异更新需要可验证的 `change_requests`。目前仅自动处理金额和付款方式的 SET；
  CLEAR 及其他字段的变更请求转复核。局部更新可以没有金额，其余字段继承历史记录。
- 每个任务独立去重，在历史账单副本中生成计划；预览不修改正式库或跨任务状态。
- 最终题目 Schema 校验及 JSON 序列化通过后才写回，失败时不修改正式内存或文件。
  提交使用排他锁文件和历史版本检查；冲突时停止并要求重建计划，不覆盖新数据。
  进程异常退出可能遗留 `.lock`；确认无写入进程后再人工清理，不自动抢占。

本次 v2 只修改事实与引用的绑定。观察的风险分级、文档级问题传播及无费用附件出口仍保持现有策略。
本地全量测试归档位于 `test-results/20261028T173750+0800-live-full-v2/summary.json`，未随代码发布。
10 个输入文件及
邮件内 3 个附件完成真实测试，240 个非空事实都有引用；7 条 CREATE 在隔离库写入并验证重复处理
不再写入，16 条结果仍待复核。因此当前通过引用完整性和写入安全验证，尚未通过完整业务验收。
已知问题包括正常数字规范化被观察门禁误拦截、免费说明误抽、无费用附件产生空复核记录，
以及部分主体/费用映射不明确和图片 OCR 金额错误；详见同目录 `findings.json`。
原始模型响应、测试快照与排查流程图仅在本地保留；v2 抽取格式以本节为准。

`check-model --report-dir <新目录>` 可保存合成探针原文、原始模型响应和适配后的结果。
它不会发送项目账单，且任何证据检查失败都会返回非零退出码。

## 匹配和冲突

JSON 账单数据在 `data/existing_bills.json`。匹配优先级：

1. 单据编号 + 标准费用名称。
2. 提单号 + 标准费用名称 + 币种。
3. 订舱号 + 标准费用名称 + 币种。
4. 箱号 + 双方 + 日期 + 金额。

决策：

- 常规匹配无候选且无混淆字符候选：`CREATE`。
- 唯一候选且相同：`NO_CHANGE`。
- 唯一候选且明确字段变化：`UPDATE`。
- 多候选、币种/交易方冲突、合计不一致、低置信度或 AR/AP 无法判定：`PENDING_REVIEW`。

在返回 `CREATE` 前，检查同一标准费用名称下的历史单据号、主/分提单号、订舱号和箱号。
如果编号在常规标准化后仅有 `0/O`、`1/I` 差异，则视为疑似 OCR 混淆候选，
返回 `PENDING_REVIEW`；即使只有一个候选、金额相同或抽取置信度较高，也不会自动确认或纠正编号。
主/分提单号可交叉比较，其他类型的编号仅在同类型字段之间比较。
已通过常规策略确定的匹配仍按原规则处理。

例如历史单据号为 `00yyc`，本次识别为 `OOyyc` 且其他策略均未匹配时，暂停新增。
内部审计 `conflicts` 保留原始识别值、标准化值、候选编号、候选字段及候选账单 ID；
标准输出的中文 `ai_reasoning` 同样展示原始识别值和候选编号、账单 ID。
同一输入重复出现也保持 `PENDING_REVIEW`，候选记录不会自动写回。

默认不修改 JSON 账单库。要写回：

```bash
ship-invoice-agent process ... --apply
```

`PENDING_REVIEW` 永远不自动写回。

## 测试与 Harness

```bash
ruff check .
pytest -q
```

单元测试不调用真实模型或 OCR。

### Excel 和 Word 样例

`tests/fixtures/documents/` 新增三组原生 `.xlsx`、`.docx` 样例，共六个文件。
它们参考已有 PDF 和图片编写，仅保留账单关键内容，不是原文件的逐字转换。

| 文件名（分别包含 `.xlsx` 和 `.docx`） | 参考样例 | 费用金额 USD | 合计 USD |
| --- | --- | --- | --- |
| `arrival-notice` | PDF 示例账单 1 | 245.00 | 245.00 |
| `debit-note` | PDF 示例账单 2 | 65.00、1310.40、35.00 | 1410.40 |
| `wharfage-invoice` | 图片示例 | 5.00、21.90 | 26.90 |

样例定义和期望结果位于 `tests/fixtures/office_samples.json`。
Arrival Notice 的单据号、部分交易方、图片账单的日期和若干辅助字段为编写数据，
所有样例均按清晰表格重建；图片 OCR 原有歧义不能据此视为已经修复。
Debit Note 保留两条不同金额的 Handling Charge，并添加文本订舱号 `00YYC` 验证前导零。

```bash
pytest tests/unit/test_office_parsers.py -q
```

这组离线测试验证原生文档路由不调用 OCR、段落和费用表格内容保留、Excel 数值类型和
公式缓存合计、前导零编号，以及使用假模型输出后的费用标准化、AR/AP 和 JSON 输出。
离线测试不验证真实模型的抽取准确率；旧式 `.xls`、`.doc` 不在本轮样例范围内。
新契约的 Office 假模型回归包含证据校验；未知 OCA 费用预期转复核。

如需调用 `.env` 配置的真实模型，可在获得文档外发授权后运行：

```bash
python scripts/verify_office_samples.py --output-dir test-results/office-live-unique-run
```

该脚本检查六个文件的费用、金额、币种、付款方式、单据和物流字段、AR/AP 及总额，
保存读取内容、模型抽取和最终输出；禁止调用 OCR，关闭账单写回，并使用独立空历史库。
输出目录必须是新目录，以免覆盖之前的验证记录。

调整标准化规则后，可复用已保存的真实模型输出做离线重放，无需重新调用模型：

```bash
python scripts/verify_office_samples.py \
  --replay-from test-results/20261025-office-native-reading \
  --output-dir test-results/office-replay-unique-run
```

重放会重新读取原生文件，校验读取内容和文件哈希与归档一致，再将保存的模型输出送入
当前标准化及业务流程；原始结果目录保持只读，新结果写入独立目录，并记录源目录文件哈希。
`Wharfage` 已作为独立标准名加入词表，大小写写法统一为 `Wharfage`，
较长的 `Wharfage Administration Fee` 及其别名仍映射至独立费用，避免被较短名称覆盖。
本次修复的重放结果和原始失败结果分别见
`test-results/20261025-office-wharfage-replay/REPORT.md` 与
`test-results/20261025-office-native-reading/REPORT.md`。

将一次真实运行的 stdout 保存后，和 Golden 比较：

```bash
ship-invoice-agent process ... > /tmp/debit-note-output.json
ship-invoice-agent evaluate \
  --actual /tmp/debit-note-output.json \
  --golden tests/golden/debit_note.json
```

评测输出包含：

- charge recall
- amount accuracy
- currency accuracy
- reference accuracy
- invalid JSON count

更换模型或 OCR 后应重新运行 Golden Harness，配置可切换不等于准确率等价。

## 安全与已知限制

- `.env` 不进入 Git。
- 文档内容被明确当作不可信数据。
- 本地 OCR 仅用于开发，不应直接暴露到公网。
- `.doc` 需要系统安装 `antiword`；推荐优先使用 `.docx`。
- Excel 支持 `.xls/.xlsx/.xlsm`。
- 不同模型/OCR 的实际效果必须通过 Harness 验证。
- 未提供记账主体时，AR/AP 无法可靠判断，记录进入 `PENDING_REVIEW`。

## 业务和技术补充思考

1. 单据号 + 费用名不是唯一键；样例中同一 Debit Note 有两条 Handling Charge。
2. Debit/Credit Note 不能直接等于 AP/AR，必须知道记账主体。
3. OCR 置信度不可跨厂商直接比较，需要基于合计闭环等业务证据计算可入账性。
4. 邮件正文和附件可能重复表达同一费用，需要文件哈希与业务指纹两层去重。
5. 文档中的罚金示例、账户号和日期都是高风险误抽取源，必须用费用行上下文约束。
6. 更新时只允许覆盖证据明确的字段，其他字段继承历史值。
7. 低置信度、多候选和强字段冲突应阻止写入，而不是强行选择一个结果。
8. 模型能够通过配置切换，但不同模型的字段准确率必须重新评测。
