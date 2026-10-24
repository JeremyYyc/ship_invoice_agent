# 本地 OCR 四源文件测试报告

## 结论

本轮验证证明以下链路可运行：项目真实源文件 → 项目文件 staging → 隔离 Docker 文件服务 URL → 本地 OCR HTTP → 项目真实 `OCRClient` → 项目真实 `DocumentRouter` → `RawDocument`。

这不是最终账单端到端测试：没有调用 LLM，没有生成或评估 `BillOperation[]`，没有调用外部 OCR，没有执行 `--apply`。由于没有完整人工标注，本报告不声明 OCR 整体准确率通过。

最终成功运行目录：`test-results/20261022T072845Z-local-ocr`

首轮环境失败证据：`test-results/20261022T072743Z-local-ocr`

## 版本与隔离配置

- 分支：`main`
- Commit：`7299678e8188629133aff19f56591096ae89f2eb`
- 成功运行：2026-10-22T07:28:48Z 至 2026-10-22T07:28:55Z
- Python client：3.13.12
- OCR container Python：3.12.13
- Tesseract：5.5.0
- Poppler `pdftoppm`：25.03.0
- OCR endpoint：一次性宿主机回环端口，`127.0.0.1:<ephemeral>/process`
- 文件服务：仅临时 Docker 网络可见，无宿主机发布端口
- OCR 请求：`task=orderChange`、`callBack=""`、`taskType=""`、timeout `(10, 300)`、最多重试 2 次
- 镜像均使用本机已有版本并指定 `--pull=never`
- 本轮只对回环地址和临时容器 DNS 设置 `NO_PROXY`；未修改主机代理、防火墙或 VPN

## 输入清单

| 顶层文件 | 大小 | SHA256 |
|---|---:|---|
| `PDF示例账单1.pdf` | 151278 | `7f0681695a278ea49dc50090bb1bc476b3c73bbc4a3d4862bc31a013d2ad045d` |
| `PDF 示例账单2.pdf` | 77705 | `70dcc3e7d07a0012fc642af256371f25438eed2c7bbe25442b7ffbd193fd34ba` |
| `sample-pre-alert.eml` | 1479995 | `fad3298ec8fd3da0e092dfbed0a313be28170073f1e01125195a5dfe9b886e58` |
| `图片示例.png` | 91964 | `f3ca6432d5597b7254eb805cb02e97e5633a681658fae4967daaa534e349fd93` |

统计口径：4 个顶层源文件。EML 展开为 1 个正文和 3 个附件；全批次共 7 个 `RawDocument`、6 个 OCR 文档和 6 次 OCR 请求。文件哈希没有重复组，因此未把重复材料误算为覆盖。

## 逐项运行结果

| 来源 | 与顶层关系 | 页数 | OCR 耗时 | 入口/响应 | 内容核验 |
|---|---|---:|---:|---|---|
| `PDF示例账单1.pdf` | 顶层 | 3 | 4.003s | 通过 | 通过稳定锚点：`CMDUCHN4053601`、`USD 245.0000`、`SAPU2040689`。文档还包含 `$25/$1100/$150` 等说明性金额，后续模型测试必须验证不误抽取。 |
| `PDF 示例账单2.pdf` | 顶层 | 1 | 0.610s | 通过 | 通过稳定锚点：`DC-108478`、`USD`、`65.00`、`1,310.40`、`35.00`、总额 `1,410.40`；与现有 Golden 的核心费用一致。 |
| `sample-pre-alert.eml` | 顶层正文 | 非 OCR | — | 路由通过 | 通过：`OOLU4055331281`、`H/C $50`、`FREIGHT PREPAID`；正文未被错误地当 PDF 提交。 |
| `InsertPic_C25B.jpg` | EML 附件 | 1 | 0.324s | 通过 | 内容质量未通过：虽为非空文本，但仅约 40 个字符且基本为乱码，不能作为可靠业务证据。 |
| `4055331281 MBL.pdf` | EML 附件 | 1 | 1.056s | 通过 | 部分内容核验通过：`4055331281`、`HZMHK331281`、`TIIU8975975`、`PREPAID`。 |
| `4055331281 HBL.pdf` | EML 附件 | 1 | 0.857s | 通过 | 部分内容核验通过：`SSZSE26093128`、`TIIU8975975`、`FREIGHT PREPAID`；`OOLU4055331281` 被识别成 `00LU4055331281`，存在 O/0 错误。 |
| `图片示例.png` | 顶层 | 1 | 0.210s | 通过 | 部分通过：`INVOICE`、`USD`、`5.00`、`21.90`、`26.90` 可见；参考号中的 `6` 被识别成 `G`，费用名称也有明显拼写噪声。 |

“入口/响应通过”表示文件 URL 下载返回 200、OCR `/process` 返回 200、本地响应 schema 正确且 `full_text` 非空；不等于业务字段准确率通过。

## 自动断言

成功运行的 11 项断言全部通过：

- 4 个顶层输入全部完成。
- EML 展开为正文加 3 个附件，3 个附件均保留 `parent_source=sample-pre-alert.eml`。
- 共 6 个 OCR 文档和 6 次项目真实 client 请求。
- 6 次请求全部成功。
- 6 份响应均符合本地 `success/task/pages/full_text` schema。
- 6 份 OCR 文本均非空。
- 顶层图片包含 `INVOICE`、`USD`。
- EML 正文包含 `OOLU4055331281`、`H/C $50`、`FREIGHT PREPAID`。

## 首轮失败与恢复

首轮运行的健康检查成功，但项目 `requests` 发往宿主机回环 OCR endpoint 的 POST 被主机代理拦截并返回 502。证据是：

- OCR 容器只收到 `/health`，没有收到任何 `/process`。
- 文件服务没有收到 GET。
- client 完整堆栈记录 `502 Server Error: Bad Gateway`。

这属于测试环境问题，不是 OCR 引擎或业务代码失败。首轮容器和网络清理后，仅为第二轮 client 进程增加 `NO_PROXY=127.0.0.1,localhost`，并为 OCR 容器增加临时文件服务 DNS 的 `NO_PROXY`，随后 6 次文件下载和 6 次 OCR 请求全部返回 200。没有修改系统级代理配置。

## 证据索引

- `summary.json`：机器可读总结果、计数、耗时、断言、错误。
- `inputs.json`：顶层输入路径、大小和 SHA256。
- `documents.json`：7 个展开文档及来源关联。
- `requests.json`：6 次请求的完整参数、URL、timeout、耗时和状态。
- `raw_ocr/*.json`：6 份完整原始 OCR JSON。
- `ocr_text/*.txt`：6 份 OCR `full_text`。
- `documents/*.txt`：router 交付给后续抽取层的完整内容，包括 EML 正文。
- `client_router.log`：staging、请求与 router 关键链路日志。
- `container-logs/file-server.log`：6 次文件 GET 及 HTTP 200。
- `container-logs/local-ocr.log`：健康检查与 6 次 `/process` HTTP 200。
- `harness-stdout.log`、`harness-stderr.log`、`errors.json`：执行日志及错误证据。
- `commands.txt`：执行命令。
- `environment/`：Git、Docker、镜像、Python、Tesseract、Poppler 和容器 inspect 信息。
- `cleanup.log`、`environment/container-leftovers.txt`、`environment/network-inspect-exit-code.txt`：清理证据。
- `evidence-sha256.txt`：结果文件校验清单。

首轮失败目录保留相同类别的运行配置、client 堆栈、容器日志和清理证据。

## 清理与 Git 状态

- 本次两个临时容器均使用 `--rm`，测试后已停止并删除。
- 临时 Docker 网络已删除；最终只读检查返回 `network ... not found`。
- 文件服务从未发布宿主机端口；OCR 只绑定随机回环端口。
- 原始输入未修改。
- 业务代码未修改、未提交、未推送。
- 仓库业务分支仍为 `main` / `7299678e8188629133aff19f56591096ae89f2eb`；仅新增未跟踪的 `test-results/` 证据目录。

## 后续建议

下一步应基于本轮 7 份 `RawDocument` 内容添加模型输出 mock 和完整 `BillOperation[]` expected JSON，重点覆盖：

1. PDF1 中说明性金额不得抽成费用。
2. HBL 的 O/0、图片参考号的 6/G 等 OCR 噪声应进入 warning 或人工复核。
3. EML 正文的 `H/C $50` 与附件内容应避免重复记账。
4. 内嵌图片低质量 OCR 不应自动入账。

