# Wharfage 标准名称修复和真实模型输出重放验证

补充独立标准名 `Wharfage` 后，六份已保存的真实模型输出重放验证全部通过，12 条费用的名称、金额、币种和付款方式均与期望逐字一致。Word 码头账单的 21.90 USD 费用由 `WHARFAGE` 统一为 `Wharfage`；5.00 USD 的 `Wharfage Administration Fee` 保持独立。

本轮重新读取原生 Excel 和 Word 文件，复用原先阿里云 `qwen3.8-flash` 的已保存输出，验证当前标准化及业务流程。外部模型调用为 0，账单写回关闭。本结果属于保存输出的离线回归，未重新测量模型抽取准确率或波动。

## 修改和单元测试

`data/charge_aliases.json` 新增 `"Wharfage": []`。通用解析器会自动将标准名自身加入忽略大小写的匹配规则，因此无需重复配置大写和小写别名。较长名称的匹配仍优先于其内部的较短名称，本次未修改解析算法或期望样例。

新增 12 个回归用例：

- 四种大小写写法均统一为 `Wharfage`，没有未知别名或歧义告警。
- `Wharfage Administration Fee`、`Wharfage Admin Fee` 及其大写写法仍映射为 `Wharfage Administration Fee`，不被 `Wharfage` 覆盖。
- 模型把这两类费用互相误分类时，原始证据优先，并触发冲突复核。
- `WHARFAGES` 和 `WHARFAGEADMINISTRATIONFEE` 不因包含字符片段而误命中标准名。

全部 110 项单元测试通过，`ruff check src tests scripts` 和 `git diff --check` 均通过。

## 重放结果

| 文件 | 原始严格结果 | 修复后严格结果 | 费用条数 | 合计 USD | 方向 |
| --- | --- | --- | ---: | ---: | --- |
| arrival-notice.xlsx | 通过 | 通过 | 1 | 245.00 | AP |
| arrival-notice.docx | 通过 | 通过 | 1 | 245.00 | AP |
| debit-note.xlsx | 通过 | 通过 | 3 | 1410.40 | AR |
| debit-note.docx | 通过 | 通过 | 3 | 1410.40 | AR |
| wharfage-invoice.xlsx | 通过 | 通过 | 2 | 26.90 | AP |
| wharfage-invoice.docx | 费用名称大小写不一致 | 通过 | 2 | 26.90 | AP |

文件级严格检查由 5/6 提升为 6/6；费用行严格比较由 11/12 提升为 12/12，没有放宽比较条件。金额、币种、付款方式、单据编号、日期、交易双方、物流字段、总额及 AR/AP 保持正确。六份文件均输出 CREATE 判定，未写入账单库。

两份码头账单中，Wharfage 的 `UNKNOWN_CHARGE_ALIAS` 告警均已消失。到港通知中 OCA 费用的同类告警仍保留，该费用不属于本次词表修复范围。

## 原始证据和写回保护

原始结果目录 `20261025-office-native-reading` 完整保留，原始报告、模型输出和 `summary.json` 中的失败状态没有覆盖。重放前后的源目录文件哈希记录于 [完整性记录](source_integrity.json)，汇总见 [分析明细](analysis.json)。

六份重放的 `extracted_document.json` 与原始归档逐字节一致；变更发生在后续标准化阶段。对照原始及修复后的操作输出，除费用名称和相关审计说明外，操作判定、单据字段、交易双方、金融属性、金额、币种和付款方式均保持不变。

默认账单库保持不变，六个独立历史库仍为原始字节 `[]`。重放检查还确认了原生读取内容和文件哈希与保存的原始读取结果一致。

- [原始真实验证报告](../20261025-office-native-reading/REPORT.md)
- [原始严格检查结果](../20261025-office-native-reading/summary.json)
- [本次严格检查结果](summary.json)
- [Word 原始模型输出](../20261025-office-native-reading/wharfage-invoice.docx/extracted_document.json)
- [Word 修复前操作输出](../20261025-office-native-reading/wharfage-invoice.docx/operations.json)
- [Word 修复后操作输出](wharfage-invoice.docx/operations.json)

## 复跑命令

```bash
python scripts/verify_office_samples.py \
  --replay-from test-results/20261025-office-native-reading \
  --output-dir test-results/office-replay-new-run
```

输出目录必须为新目录，且不能位于原始归档目录内。重放模式直接加载保存的模型输出，不创建外部模型客户端；每个文件均使用独立空历史库，并显式传入 `apply=False`。
