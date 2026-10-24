# 题目标准输出格式端到端验证报告

## 结论

默认输出已严格切换为题目规定的 JSON 数组格式。10/10 条真实端到端结果通过
`AssignmentBillOperation` 严格 Schema，未发现多余顶层字段，`audit_trail` 只包含
`ai_reasoning`。

## 输出配置

```dotenv
JSON_OUTPUT_INDENT=2
OUTPUT_SCHEMA_MODE=assignment
```

交付与验收环境必须使用 `assignment`。`internal` 仅用于本地调试和保存完整诊断信息。

## 标准字段

每条记录严格包含：

```text
operation
document_info
  document_type
  reference_no
  document_date
transaction_party
  issuer
  recipient
charge_details
  charge_item
  amount
  currency
  payment_term
financial_attributes
  accounting_entity
  posting_type
audit_trail
  ai_reasoning
```

内部的 `source_file`、`confidence`、`warnings`、`changed_fields` 和 `conflicts`
不会作为额外字段出现在标准输出中。需要对外展示的信息会转换为
`audit_trail.ai_reasoning` 条目。

例如费用名称冲突在标准输出中表现为：

```json
"audit_trail": {
  "ai_reasoning": [
    "original_charge_item alias mapped deterministically to Handling Charge",
    "model proposed Terminal Handling Charge; deterministic evidence took precedence",
    "warning: MODEL_CHARGE_NAME_CONFLICT",
    "warning: MODEL_CHARGE_NAME_OVERRIDDEN"
  ]
}
```

## 回归结果

- 场景：4/4 完成
- 模型抽取：7/7 成功
- 最终标准记录：10
- 标准 Schema：10/10 通过
- 总耗时：82.40 秒
- 超时/重试：0
- `--apply`：未使用
- 历史账单：未修改

PDF2 Golden：

| 指标 | 结果 |
|---|---:|
| Charge recall | 1.0 |
| Amount accuracy | 1.0 |
| Currency accuracy | 1.0 |
| Reference accuracy | 1.0 |
| Invalid JSON | 0 |

## 内部诊断

业务代码内部仍使用完整 `BillOperation`，因此匹配、写回、冲突处理和测试不会因公共输出裁剪
而丢失数据。必要时可在本地设置 `OUTPUT_SCHEMA_MODE=internal` 查看完整诊断，但该模式不用于
题目提交。
