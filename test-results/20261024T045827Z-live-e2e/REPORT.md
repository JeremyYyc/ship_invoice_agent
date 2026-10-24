# 本地 OCR + 真实 LLM 端到端测试报告

## 结论

本轮首次完整跑通了：

```text
本地样例文件
  -> Docker 文件服务
  -> 本地 Tesseract OCR HTTP
  -> RawDocument
  -> 阿里云百炼 qwen3.8-27b
  -> ExtractedDocument
  -> 标准化 / 校验 / AR-AP / 匹配
  -> BillOperation[] JSON
```

- 配置场景：4
- 成功场景：4
- 失败场景：0
- 本地 OCR 请求：6
- 真实 LLM 抽取：7
- 最终账单操作：10
- 总耗时：1298.98 秒（约 21 分 39 秒）
- `--apply`：未使用
- `data/existing_bills.json`：未修改

所有 JSON 文件均可解析；10 条最终记录均通过 `BillOperation`
Pydantic Schema，7 份模型抽取均通过 `ExtractedDocument` Schema。

## 环境与安全

- LLM Provider：`qwen`
- Model：`qwen3.8-27b`
- API Host：`dashscope.aliyuncs.com`
- Structured Output：`prompt + Pydantic`
- OCR：本地 Docker Tesseract
- OCR Endpoint：`http://127.0.0.1:18001/process`
- 文件服务：`http://file-server:8081`
- API Key 仅在运行时读取，未写入任何测试结果。

模型 JSON 连通性检查和所有最终成功模型请求均收到 HTTP 200。

## 场景结果

### PDF 1：CMA CGM General Notice

- 结果：成功
- 操作：`CREATE`
- 单据/提单号：`CMDUCHN4053601`
- 费用：`On Carriage Additional - US Em`
- 金额：`USD 245.00`
- AR/AP：`AP`
- 费用来源证据：第 1 页明细行

关键安全结果：模型没有把文档条款中的 `$25`、`$1100`、`$150`
误抽为实际费用。

### PDF 2：Debit Note

- 结果：成功
- 费用条数：3
- `Handling Charge`：`USD 65.00`
- `Additional Detention Free Time Charge`：`USD 1310.40`
- `Handling Charge`：`USD 35.00`
- 合计：`USD 1410.40`

Golden 评测：

| 指标 | 结果 |
|---|---:|
| Charge recall | 1.0 |
| Amount accuracy | 1.0 |
| Currency accuracy | 1.0 |
| Reference accuracy | 1.0 |
| Invalid JSON | 0 |

三条操作均为 `PENDING_REVIEW`，原因不是费用抽取错误，而是本次测试传入的
记账主体是 `Pilot Aiship Tech co., Ltd.`，而模型从文档中识别的出票/收款方是
`NEPTUNE SHIPPING LIMITED`，所以确定性规则无法解析 AR/AP，正确地阻止了自动入账。

### EML 及附件

- 结果：成功
- 展开文档：邮件正文 + 内嵌图片 + MBL + HBL
- 最终操作：4 条 `PENDING_REVIEW`
- 邮件正文提取出 `USD 50`，但置信度为 `0.65`
- 三个附件未提取费用，没有自动记账

该结果体现了低置信度和记账主体角色不明时的安全降级。

### 图片账单

- 结果：成功
- 识别费用：`USD 5.00` 和 `USD 21.90`
- 识别合计：`USD 26.90`
- 最终操作：2 条 `PENDING_REVIEW`

OCR 噪声较高，模型对两条费用分别给出 `0.55` 和 `0.50` 置信度，
低于项目 `0.85` 自动接受阈值，因此均进入人工复核。

## 性能观察

| 模型抽取 | 耗时 |
|---|---:|
| PDF 1 | 104.66s |
| PDF 2 | 93.29s |
| EML 正文 | 109.66s |
| EML 内嵌图片 | 14.63s |
| MBL | 64.72s |
| HBL | 426.50s |
| 图片账单 | 471.59s |

HBL 和图片账单均经历两次超时后在最后一次重试成功。当前
`LLM_TIMEOUT_SECONDS=120`、`LLM_MAX_RETRIES=2`，这是本轮总耗时较长的主要原因。

## 验收边界判定

| 边界 | 状态 | 证据 |
|---|---|---|
| 本地文件 -> 文件 URL | 通过 | 6 次 OCR 文件请求 |
| 文件 URL -> 本地 OCR | 通过 | 6 份原始 OCR JSON |
| OCR/EML -> RawDocument | 通过 | 4 份顶层 RawDocument 集合 |
| RawDocument -> 真实 LLM | 通过 | 7 份真实模型抽取 |
| LLM -> Pydantic Schema | 通过 | 7/7 结构合法 |
| 抽取 -> BillOperation | 通过 | 10/10 结构合法 |
| PDF2 Golden | 通过 | 四项准确率 1.0，invalid JSON=0 |
| 历史账单写回 | 未执行 | `apply=false`，确保测试不污染数据 |

## 结果文件

- `summary.json`：整体机器可读结果。
- `cases.json`：每个场景的状态、耗时和操作数。
- `configuration.json`：已脱敏的配置摘要。
- `environment.json`：Python、依赖、Git 和 Docker 状态。
- `model_check.json`：真实模型 JSON 能力检查。
- `raw_ocr/`：6 份本地 OCR 原始 JSON。
- `raw_documents/`：送入抽取层的 `RawDocument`。
- `model_extractions/`：7 份真实 LLM 结构化抽取。
- `outputs/`：4 份最终 `BillOperation[]`。
- `evaluation_pdf2.json`：PDF2 Golden 评测。
- `runtime.log`：模型 HTTP、重试和场景日志。
- `docker_logs.json`：OCR 与文件服务容器日志。
- `harness/live_e2e_harness.py`：可复现测试 Harness。

## 后续建议

1. 将 PDF2 验收命令中的记账主体改为文档实际的 `Neptune Shipping Limited`，
   或明确业务上谁才是记账主体。
2. 对 HBL/图片账单考虑缩减重复 OCR 文本、限制页面输入或调整模型超时。
3. EML 正文的 `H/C $50` 费用名称抽取为 `SHA pay TAR`，应增加场景 Prompt
   或后处理规则。
4. 保持当前低置信度不自动入账的安全策略。
