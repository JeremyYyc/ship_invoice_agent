# Excel 和 Word 真实抽取验证报告

2026 年 10 月 25 日，六份原生 Office 样例均完成读取和阿里云模型抽取，产生 12 条费用输出。
严格检查通过 5/6 个文件；另一份 Word 的一条费用名仅在大小写上与期望不同。
12 条费用的金额、币种和付款方式全部符合期望，六份文件的单据字段、物流字段、总额和 AR/AP 均通过。
本轮未修改费用别名、期望数据或抽取逻辑，也未通过重跑筛选结果。

## 配置和执行范围

- 模型：`qwen3.8-flash`，服务：`dashscope.aliyuncs.com`，Thinking 关闭。
- 使用真实原生解析器、真实模型、标准化、匹配和输出流程；OCR 被禁止调用，六份文件均通过该检查。
- 每个文件使用独立的空 JSON 历史库，调用显式传入 `apply=False`。
- 记账主体：到港通知为 Neptune Shipping Limited，Debit Note 为 Pilot Aiship Tech co., Ltd.，码头账单为 USIAHALLAM。
- 全程耗时 42.28 秒。六个模型抽取均成功返回，无超时或 JSON 解析失败。

## 逐文件结果

| 文件 | 费用条数 | 合计 USD | 方向 | 判定 | 耗时秒 | 严格结果 |
| --- | ---: | ---: | --- | --- | ---: | --- |
| arrival-notice.xlsx | 1 | 245.00 | AP | CREATE | 7.85 | 通过 |
| arrival-notice.docx | 1 | 245.00 | AP | CREATE | 4.44 | 通过 |
| debit-note.xlsx | 3 | 1,410.40 | AR | CREATE | 8.88 | 通过 |
| debit-note.docx | 3 | 1,410.40 | AR | CREATE | 9.29 | 通过 |
| wharfage-invoice.xlsx | 2 | 26.90 | AP | CREATE | 5.04 | 通过 |
| wharfage-invoice.docx | 2 | 26.90 | AP | CREATE | 6.65 | 费用名大小写不一致 |

六份文件来自三种账单内容各自的 Excel 和 Word 版本。12 条是本轮输出条数，不代表 12 条不同的真实业务费用。
每个文件都从独立空库开始，所有 CREATE 均为判定输出，没有写入账单库。

## 指标和差异

| 检查项 | 结果 |
| --- | --- |
| 费用条数 | 6/6 个文件符合期望，共 12 条 |
| 费用名称连同金额币种付款方式逐字比较 | 11/12 条符合期望 |
| 仅忽略费用名大小写后的比较 | 12/12 条符合期望，仅用于诊断 |
| 金额 币种 付款方式 | 12/12 条符合期望 |
| 单据编号 日期 交易双方 | 6/6 个文件符合期望 |
| 提单号 订舱号 箱号及未知空值 | 6/6 个文件符合期望 |
| 总额 AR/AP 操作判定 | 6/6 个文件符合期望 |
| OCR 调用 | 0 |

`wharfage-invoice.docx` 中 21.90 USD 的费用输出为 `WHARFAGE`，期望为 `Wharfage`；
金额、币种、付款方式和业务含义一致。`charge_aliases.json` 配置了 Wharfage Administration Fee，
但未配置 Wharfage 的标准名称映射，故标准化保留模型名称并产生 `UNKNOWN_CHARGE_ALIAS`。
当前匹配器会标准化费用名称的大小写，因此本差异不会仅因大小写导致历史匹配失败，但严格输出检查仍未通过。
后续可为 Wharfage 补充标准别名，使输出名称稳定；本轮保留原始失败状态。

两个 Debit Note 均保留金额为 65.00 和 35.00 的两条 Handling Charge，没有误合并；
订舱号 `00YYC` 的前导零和两个箱号也均保留。
到港通知的 OCA 费用、两份码头账单的 Wharfage 均有 `UNKNOWN_CHARGE_ALIAS` 告警，
这些费用名未被当前别名表命中，现行策略允许保留名称并输出 CREATE。

## 账单写回保护

默认 `data/existing_bills.json` 的运行前后 SHA256 相同：

`5239a7754876b9a70871c45e41f1bd9abbd91ed2de67af926c1cfcc9c9377d51`

六个独立 `existing_bills.json` 文件在运行后仍为原始字节 `[]`。
六份输入文件的当前 SHA256 与读取时记录的哈希一致。详见 [分析明细](analysis.json)。

## 原始证据和复跑

- [严格检查汇总](summary.json) 保留 `passed=false` 和失败文件的 `charge_values=false`。
- [指标及逐条输出](analysis.json) 将严格检查与忽略大小写的诊断分开记录。
- 每个以文件名命名的目录均含 `raw_document.json`、`extracted_document.json`、`operations.json` 和 `assignment.json`。
- [差异的模型抽取](wharfage-invoice.docx/extracted_document.json) 和 [差异的最终输出](wharfage-invoice.docx/operations.json) 可直接核对。

复跑命令需要使用新目录，避免覆盖本轮结果：

```bash
python scripts/verify_office_samples.py --output-dir test-results/office-live-new-run
```

本轮样例由已有 PDF 和图片内容重新编写，原生文本清晰可读；结果适用于这些 `.xlsx` 和 `.docx` 样例。
本轮不验证原 PDF 或图片的 OCR 质量，也不覆盖旧式 `.xls` 和 `.doc` 文件。
