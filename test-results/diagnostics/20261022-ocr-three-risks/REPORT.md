# OCR 三类风险证据诊断

## 范围与结论

本报告只复用 `test-results/20261022T072845Z-local-ocr` 中已经生成的 OCR JSON、文本和原始输入；没有重新调用 OCR 服务、外部 OCR 或 LLM，也没有生成最终 `BillOperation[]`。

| 问题 | 状态 | 准确结论 |
|---|---|---|
| 说明性金额误抽 | 已证实高风险输入；下游误抽尚未验证 | `$25/$1100/$150` 确实是条款/例子，OCR 完整保留并因 JSON 多表示重复放大；没有模型输出，不能称为已误抽。 |
| O/0 编号歧义 | 已在 OCR 层发生；下游选择尚未验证 | HBL 原图为 `OOLU4055331281`，OCR 为 `00LU4055331281`。现有标准化和校验不会识别或安全修复该错误。 |
| “低质量内嵌图片” | 上一轮定性需修正 | 该 JPEG 是 HTML `cid:` 引用的邮件营销签名图，不是业务单据。真正已发生的是 Router 将 inline 装饰图送入 OCR；没有证据显示模型据此编造账单。 |

## 1. 说明性金额

### 状态

**已发生：** 说明性金额进入 OCR 原始 JSON和抽取层输入。

**尚未发生/未验证：** 没有运行真实模型，因此没有证据表明 `$25`、`$1100` 或 `$150` 已被抽为费用。

### 原始像素与 OCR 证据

`PDF示例账单1.pdf` 原图版面清楚区分：

- 第 1 页顶部 `GENERAL NOTICE` 写明取消 I.T. 的 `SERVICE FEE OF $25.00 PER CANCELLED I.T.`，属于政策说明。
- 第 1 页底部独立账单行才是 `On Carriage Additional ... USD 245.0000`，并有 `TOTAL CHARGES TO BE PAID: USD 245.0000`。
- 第 2 页 `PLEASE NOTE` 条款中包含误用费 `$1100` 和司机等待费 `$150 per hour`，都不是本票实际费用。

OCR 文本保留了这些上下文，但本地响应同时包含 `pages[].text`、`pages[].lines` 和 `full_text`，主程序把完整 JSON 原样交给模型。因此抽取输入中：

- `$25.00` 出现 9 次：原文三页各重复一次，再由三种响应表示重复。
- `$1100`、`$150` 各出现 3 次。
- 真正费用 `USD 245.0000` 出现 6 次。

### 直接机制与根因

已证实：

1. 原文确实包含容易被误认为费用的金额。
2. Tesseract 只输出纯文本和行，不保留坐标、表格单元格或版面区域。
3. `OCRResponse` 同时携带同一内容的 text、lines 和 full_text；主程序没有去重。
4. Prompt 已明确禁止将 disclaimer、tariff、penalty example 当费用，这是已有防护。
5. `ExtractedCharge.source_text/source_page` 是可选字段；schema 不要求每条费用提供可核对证据。
6. 确定性校验只检查负数、币种、简单字符混淆和合计；没有判断“金额是否位于真实费用行”。

合理推测，尚未实测：重复出现会增加条款金额在模型上下文中的显著性；失去坐标和表格结构会增加模型仅凭邻近词猜测费用的概率。

### 最小修复

1. 在送模型前只保留一种文本表示，例如按页的 `pages[].text`，不要同时发送 `lines` 和 `full_text`；保留明确的 `[PAGE n]` 分隔。
2. 每个 charge 强制要求 `source_page` 和逐字/近似可回溯的 `source_text`；缺失证据则 `PENDING_REVIEW`。
3. 对存在 `TOTAL CHARGES TO BE PAID` 的文档，强制做费用和合计闭环；合计缺失但 OCR 明确存在时也进入复核。
4. 增加区段上下文：`GENERAL NOTICE`、`PLEASE NOTE`、bank/remittance 等区段中的金额默认不是费用候选；不要仅按金额正则全局删除。
5. 企业 OCR 若能给坐标/表格结构，应保留其行列和 bounding box，而不是压平成纯文本。

### 回归验收

- 使用本次完整 OCR fixture，模型 mock/抽取结果只能产生 `USD 245.0000` 的实际费用。
- `$25/$1100/$150` 均不得成为 charge。
- 每条 charge 必须包含来源页和原文行，并能在对应页 OCR 中定位。
- 构造包含上述三条错误费用的模型 mock 时，确定性校验必须拒绝自动入账或转 `PENDING_REVIEW`。
- 输入去重后每个原始文本片段只出现一次，不再因 local OCR schema 重复三份。

## 2. O/0 编号歧义

### 状态

**已发生于 OCR 层。** HBL 原始像素清晰显示 `EXPORT REFERENCES OOLU4055331281`；保存的 OCR 文本为 `EXPORT REFERENCES 00LU4055331281`。

**尚未验证：** 没有运行模型，因此未知模型会选择 OCR 错值、从 EML 正文纠正，还是产生其他值。

### 交叉证据

- HBL 原图：`OOLU4055331281`。
- HBL OCR：`00LU4055331281`。
- EML subject/body：`OOLU4055331281`，是独立的非 OCR 文本证据。
- MBL 附件和文件名包含核心数字 `4055331281`。
- 箱号 `TIIU8975975` 在正文和两个提单附件中一致。

### 直接机制与根因

已证实：

1. 本地 OCR 对 PDF 固定以 200 DPI 渲染，使用 `pytesseract.image_to_string(..., lang="eng")` 的默认页面分割。
2. `O` 与 `0` 在该字体和编号上下文中形状相近；OCR 确实混淆了前两位。
3. 本地 schema 丢弃了 Tesseract 的 word confidence、坐标和候选字符。
4. `normalize_identifier()` 只做 Unicode 归一化、转大写和移除非字母数字，不纠正也不标记 O/0。
5. 当前 OCR 混淆校验只检查 charge 的 `source_text` 中少量模式，不检查提单号等 document identifier，也不覆盖 `00LU`。
6. Workflow 按每个 `RawDocument` 分别抽取，没有先把 EML 正文、MBL、HBL 的同一编号做证据融合。

可能因素，尚未独立验证：提高 DPI、局部裁剪、不同 PSM 或图像增强可能改善识别；不能在没有对照实验时承诺有效。

### 最小修复

1. **禁止全局 O→0 或 0→O。** 只在已知 identifier 字段生成有限歧义候选。
2. 先做跨来源证据投票：优先采用 EML 原生文本、多个独立附件和已知 carrier/SCAC 前缀共同支持的值。
3. 保存 `observed_value`、候选值、来源和冲突；只有多个独立证据一致时才自动选择。
4. 若只有单份 OCR 或证据冲突，保留原观测值并增加 `IDENTIFIER_O0_AMBIGUOUS`，强制 `PENDING_REVIEW`。
5. 本地 OCR 后续可改用 `image_to_data` 保存 token confidence 和坐标；低置信度 identifier 进入复核。

### 回归验收

- 本次 EML + HBL 组合应在审计记录中保留 `00LU...` 原观测值，同时因 EML 明文佐证解析为 `OOLU...`。
- 单独处理 HBL 时不得静默改写为 `OOLU...`；必须标记歧义并复核。
- 不相关编号中的合法 `0` 或 `O` 不得被批量替换。
- 匹配历史账单前应使用经证据确认的 canonical identifier，并保留来源链。

## 3. Inline JPEG 与“低质量图片”

### 状态与定性修正

上一轮将 `InsertPic_C25B.jpg` 表述为“低质量图片内容识别失败”不够准确，应修正为：

- JPEG 是 1500×770 的清晰营销签名横幅，包含二维码、金门大桥和“中美跨境·数智工具”等宣传内容。
- MIME 为 `image/jpeg`，有 `Content-ID`，无 `attachment` disposition。
- HTML 通过 `<img src="cid:...">` 引用它，显示尺寸 450×231。
- 它不是发票、账单或提单，不应该进入业务 OCR。

**已发生：** 现有 parser 因其有 filename，将它加入 attachments；Router 又因 `.jpg` 属于 OCR suffix 而发送 OCR。

**尚未发生/未验证：** OCR 返回约 40 字符乱码，但没有运行模型，因此没有证据表明它已编造费用或账单。

### 直接机制与根因

1. `parse_email()` 只判断 `part.get_filename()`，不保留 disposition、Content-ID、MIME role 或 HTML cid 引用关系。
2. Router 对所有支持的图片附件一视同仁，没有 inline/装饰图片过滤。
3. OCR 默认语言是 `eng`，营销图主要包含中文和复杂背景，因此英文 OCR 输出很差；但这不是核心故障，因为它本来就不该被当业务文档。
4. 在模型前没有 document-level 质量门控。Workflow 的 confidence 是模型为每条 charge 提供的值，不是 OCR 质量分数。

### 最小修复

1. 扩充 EML attachment 元数据：保留 content type、disposition、Content-ID、inline 标记和 HTML cid 引用。
2. 默认跳过 HTML 引用的 inline 图片，记录 `SKIPPED_INLINE_DECORATION`；真正 `attachment` 的 PDF/图片继续处理。
3. 为可能是扫描件的 inline 图片保留例外：当用户显式启用，或图片具有发票/金额/币种/表格等业务特征时，进入 OCR 后转人工复核，而不是静默丢弃。
4. 增加 document-level gate：结合 MIME role、OCR token/confidence、金额/币种/单据标题等信号；不能仅凭文本短或字符少就判坏，因为小票可能很短。
5. 顶层 `图片示例.png` 必须继续处理。它是业务发票，虽然 OCR 有 6/G 和费用名噪声，应进入质量 warning/review，而不是被 inline 过滤规则误伤。

### 回归验收

- 当前 EML 应产生正文 + 2 个 PDF 业务附件；营销 JPEG 被跳过并留下可审计原因。
- 全批次 OCR 请求应由 6 次降为 5 次，但顶层 PNG 仍必须 OCR。
- 一个 `Content-Disposition: attachment` 的扫描发票图片仍会处理。
- 一个 inline 但具备强业务特征的扫描件不得被无声丢弃，应处理或进入人工复核。
- 营销 JPEG 不得生成 charge 或账单 operation。

## 修复优先级

1. **P0：过滤 cid inline 装饰图并保留 MIME 元数据。** 这是已证实的无效 OCR 调用，可直接降低噪声与成本。
2. **P0：identifier O/0 跨来源证据融合；无充分证据则 review。** 直接影响账单匹配和去重。
3. **P0：要求 charge source evidence，并用合计闭环阻止说明性金额自动入账。**
4. **P1：去除 OCR JSON 的 text/lines/full_text 重复，只保留带页界的单一表示。**
5. **P1：保存 OCR token confidence/bounding boxes，并针对低置信度编号和表格行建立质量门控。**

## 关键代码位置

- EML filename 即附件：`src/ship_invoice_agent/parsers/email.py:20-28`
- EML 附件全部递归路由：`src/ship_invoice_agent/parsers/router.py:77-111`
- PDF 固定 200 DPI、Tesseract plain text：`local_ocr/engine.py:19-29`
- OCR schema 重复 text/lines/full_text：`local_ocr/schemas.py:11-21`
- Prompt 的 disclaimer 防护：`prompts/extract_document.md:7-18`
- charge evidence 字段可选：`src/ship_invoice_agent/models/extracted.py:6-16`
- identifier 仅做字符清理：`src/ship_invoice_agent/normalization/service.py:16-19,52-64`
- 校验未覆盖 identifier 和费用行上下文：`src/ship_invoice_agent/validation/rules.py:28-44`
- 模型 confidence 低于阈值才 review：`src/ship_invoice_agent/workflow/agent.py:90-100`

