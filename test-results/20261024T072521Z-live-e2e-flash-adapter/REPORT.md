# qwen3.8-flash + LocalOCRAdapter 端到端测试报告

## 配置

- Model：`qwen3.8-flash`
- Thinking：`false`
- OCR Adapter：`LocalOCRAdapter`
- 单次超时：115 秒
- 每份文档总预算：235 秒
- 应用层重试：最多 1 次
- Provider SDK 重试：0 次
- Docker：使用当前 `.env` 强制重建后运行

## 结论

本轮 4/4 场景完成，7/7 文档均在第一次模型调用中成功，没有超时或重试。
整轮耗时 60.95 秒，PDF2 Golden 的费用召回、金额、币种和 reference accuracy
均为 1.0。

## Adapter 效果

| 指标 | Adapter 前 | Adapter 后 | 变化 |
|---|---:|---:|---:|
| OCR 内容字符数 | 50,025 | 15,968 | -68.08% |

Adapter 后内容只保留逐页文本和 `[PAGE n]` 标记；原始 OCR JSON 仍完整保存在
`raw_ocr/`，用于审计和复现。

## 性能

| 抽取对象 | 模型耗时 |
|---|---:|
| PDF1 | 6.92s |
| PDF2 | 11.39s |
| EML 正文 | 5.99s |
| 内嵌图片 | 4.74s |
| MBL | 5.77s |
| HBL | 6.27s |
| 图片账单 | 8.33s |

历史对比：

| 运行 | 总耗时 | 成功抽取 | 超时/重试 | PDF2 recall |
|---|---:|---:|---:|---:|
| qwen3.8-27b，原始 OCR JSON | 1298.98s | 7/7 | HBL/图片多次重试 | 1.0 |
| qwen3.8-27b，115/235/1 预算 | 948.32s | 5/7 | HBL/图片预算耗尽 | 1.0 |
| qwen3.8-flash，Adapter，关闭 thinking | 60.95s | 7/7 | 0 | 1.0 |

本轮比第一轮总耗时减少约 95.3%。由于模型、Adapter 和 thinking 配置同时变化，
该提升不能完全归因于单一因素。

## 业务结果

- PDF1：正确创建 `USD 245.00`，未误抽条款金额。
- PDF2：三条费用和金额全部正确；因测试记账主体不匹配，保持人工复核。
- EML：正文、内嵌图片、MBL、HBL 均完成抽取。
- 图片：识别 `USD 5.00 + USD 21.90 = USD 26.90`，因 OCR 证据噪声进入人工复核。

## 仍需关注

Flash 将邮件中的 `H/C $50` 解释为 `Terminal Handling Charge (THC)`，而项目别名表中
`H/C` 对应 `Handling Charge`。该结果虽然因 AR/AP 无法解析而进入人工复核，没有自动入账，
但说明费用名称仍应优先由确定性别名规则根据 `original_charge_item/source_text` 规范化，
不能完全相信模型生成的 `charge_item`。

## 数据安全

- 未使用 `--apply`。
- `data/existing_bills.json` 未修改。
- 测试配置仅记录 API Key 是否存在，不保存 API Key。
