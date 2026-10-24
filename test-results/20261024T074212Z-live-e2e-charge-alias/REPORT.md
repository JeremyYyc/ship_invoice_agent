# 费用别名证据优先解析回归报告

## 配置

- 分支：`fix/charge-alias-precedence`
- Model：`qwen3.8-flash`
- Thinking：`false`
- OCR Adapter：`LocalOCRAdapter`
- 费用冲突策略：`review`
- `--apply`：未使用

## 端到端结果

- 场景：4/4 完成
- 模型抽取：7/7 成功
- OCR 请求：6
- 最终账单操作：10
- 总耗时：78.30 秒
- 超时/重试：0
- 历史账单：未修改

PDF2 Golden：

| 指标 | 结果 |
|---|---:|
| Charge recall | 1.0 |
| Amount accuracy | 1.0 |
| Currency accuracy | 1.0 |
| Reference accuracy | 1.0 |
| Invalid JSON | 0 |

## 费用别名回归

本轮实时模型直接输出：

```text
original_charge_item = SHA pay TAR：H/C $50
charge_item = Handling Charge
```

因此最终名称为 `Handling Charge`，且系统没有产生模型冲突 warning。这是正确行为：
模型与原始证据的确定性映射一致时不应制造虚假冲突。

为了稳定验证错误路径，Harness 复用了上一轮已经保存的真实模型输出：

```text
model charge_item = Terminal Handling Charge (THC)
original evidence = H/C
```

解析结果：

- 最终名称：`Handling Charge`
- `conflict=true`
- `requires_review=true`
- warning：`MODEL_CHARGE_NAME_CONFLICT`
- warning：`MODEL_CHARGE_NAME_OVERRIDDEN`

`alias_regression.json` 的最终状态为 `passed=true`。

## 安全边界

- 唯一原始证据映射优先于模型名称。
- 模型与证据冲突时，确定性名称覆盖模型，并默认进入人工复核。
- 同一行匹配多个不同标准名称时标记 `AMBIGUOUS_CHARGE_ALIAS`。
- 同一标准名称的多个同义词不视为歧义。
- 别名仅按完整 token 匹配，`THCHARGE` 不会误命中 `THC`。
- 同一别名在配置中映射到多个标准名称时，应用初始化立即失败。

## 额外观察

本轮模型将 PDF2 的 issuer 识别为 `Pilot Aiship Tech co., Ltd.`，因此三条费用被判定为
AR `CREATE`；上一轮模型将主体角色识别为 Neptune，结果为 `PENDING_REVIEW`。费用、金额、
币种和 reference Golden 均保持 1.0，这属于模型交易方字段波动，与本次费用别名修复无关。
