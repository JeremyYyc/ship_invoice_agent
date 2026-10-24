# Ship Invoice Agent

Python + LangChain 航运智能账单助手，支持从 EML、PDF、图片、Excel 和 Word 中提取费用，使用
JSON 历史账单完成新增、更新、去重、冲突检测和 AR/AP 判断。

## 首次迭代的边界

- PDF/图片调用题目给定的 OCR HTTP 请求契约。
- 本地提供一个使用 Tesseract 的黑盒 OCR 服务。
- 主程序不读取任何厂商特定 OCR JSON 字段，而是将清理后的完整 JSON 交给抽取 Chain。
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
       LangChain + Pydantic parser
                  |
          ExtractedDocument
                  |
 normalization / validation / matching
                  |
 CREATE / UPDATE / NO_CHANGE / PENDING_REVIEW
                  |
          strict JSON array
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
LLM_MODEL_NAME=qwen-plus
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

第一版默认 `LLM_STRUCTURED_OUTPUT_MODE=prompt`，通过 JSON 指令 + Pydantic 校验获得更好的跨模型
兼容性，不强依赖厂商原生 JSON Schema。

模型调用默认采用 115 秒单次超时、每份 `RawDocument` 235 秒总预算以及一次应用层重试。
Provider SDK 的内部重试被关闭，避免重试次数相乘。两次调用均失败或剩余预算不足时，
当前文档输出 `PENDING_REVIEW`，并继续处理同一输入中的其他附件。三个参数可通过
`LLM_TIMEOUT_SECONDS`、`LLM_TOTAL_TIME_BUDGET_SECONDS` 和 `LLM_MAX_RETRIES` 调整。

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

企业环境中只替换 `OCR_API_URL` 和每个文件的 `file_url`。

## 使用

先检查模型的 JSON 能力：

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
      "source_file": "PDF 示例账单2.pdf",
      "confidence": 0.96,
      "ai_reasoning": [],
      "warnings": []
    }
  }
]
```

缺失字段使用 `null`，金额为 JSON number。

## 匹配和冲突

JSON 账单数据在 `data/existing_bills.json`。匹配优先级：

1. 单据编号 + 标准费用名称。
2. 提单号 + 标准费用名称 + 币种。
3. 订舱号 + 标准费用名称 + 币种。
4. 箱号 + 双方 + 日期 + 金额。

决策：

- 无候选：`CREATE`。
- 唯一候选且相同：`NO_CHANGE`。
- 唯一候选且明确字段变化：`UPDATE`。
- 多候选、币种/交易方冲突、合计不一致、低置信度或 AR/AP 无法判定：`PENDING_REVIEW`。

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
