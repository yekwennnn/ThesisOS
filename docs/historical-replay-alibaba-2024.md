# 阿里巴巴 2024 年 5 月 ThesisDiff 历史回放

本回放是 ThesisOS V0 的第一条完整 golden case。它重建了一个严格受时间截点约束的研究过程：先以阿里巴巴 2024 年 2 月 7 日发布的 2023 年 12 月季度业绩公告形成用户确认的 Thesis V1，再只引入 2024 年 5 月 14 日发布的 2024 年 3 月季度及 2024 财年业绩公告，生成待审 ThesisDiff，最后通过显式 `accept` 审阅形成不可变的 Thesis V2。

它不是投资建议，也不表达买卖动作、目标价或仓位建议。它的用途是验证引用、假设映射、历史时间边界、反方论证和人工确认边界。

## 时间边界

- 分析截点固定为 `2024-05-15T00:00:00Z`。
- 基准分析截点单独固定为 `2024-02-07T12:00:00Z`；V1 只能使用该时刻前公开的基准来源与证据，不能借用 5 月材料回填初始判断。
- 基准材料的 `published_on` 为 `2024-02-07`，fixture 采用保守可用时间 `2024-02-07T09:00:00Z`。
- 新材料的 `published_on` 为 `2024-05-14`，fixture 采用保守可用时间 `2024-05-14T11:00:00Z`。
- 两个可用时间均晚于 PDF 内嵌生成时间向上取整后的 UTC 时间；它们是回放用的保守边界，不声称是官网精确到秒的上线时间。
- Diff、审阅和 V2 均不使用 2024 年 5 月 15 日 00:00 UTC 之后的信息。来源、证据、预期结论和问题列表中没有事后数据。

## 官方来源与快照身份

两份 PDF 均直接来自阿里巴巴官方域名。仓库只保存元数据、官方 URL 和内容哈希，不提交 PDF 大文件。

| 角色 | 官方公告 | PDF 页数 | 字节数 | SHA-256 |
|---|---|---:|---:|---|
| 基准来源 | `Alibaba Group Announces December Quarter 2023 Results` | 29 | 742,346 | `e0e1578d01d16841a0c6242068abd3c6a43efa4d3039ecbb72ad708d04124257` |
| 新来源 | `Alibaba Group Announces March Quarter and Full Fiscal Year 2024 Results` | 40 | 845,827 | `f2a35600ac20f5a34506fc3d7ec6b91f67f1f0d4a195be2420e83d3a8b1466e5` |

每条 Citation 都重复写入对应快照哈希，避免页面更新或同名文件替换后仍被误认为同一来源。SourceDocument 还保存 `page_count`（29/40）；页码统一按 PDF 的 1-based 实际页序号，不采用文档内部章节编号，评测器会拒绝超出快照页数的 page/table locator。

## 页级取证地图

### 2024 年 2 月 7 日基准公告

| PDF 页 | 回放使用的事实 |
|---:|---|
| 1 | 季度收入人民币 2603.48 亿元，同比增长 5%；管理层表示将增加核心业务投入。 |
| 2 | 淘天收入增长 2%、订单和 88VIP 变化；云收入增长 3% 及退出低毛利项目方向；自由现金流下降 31%。 |
| 4 | 2023 日历年流通股净减少 3.3%，以及回购授权增加。 |

### 2024 年 5 月 14 日新公告

| PDF 页 | 回放使用的事实 |
|---:|---|
| 3 | 淘天 GMV、订单、购买人数、购买频次和 88VIP；公共云产品增长；AIDC 收入和订单。 |
| 4 | 财年回购金额及 3 月季度流通股净减少。 |
| 8 | 淘天、客户管理收入和云收入的分部表格。 |
| 9 | 淘天货币化率变化；淘天、云和 AIDC 调整后 EBITA 表格。 |
| 10 | 淘天投入对调整后 EBITA 的影响，以及云产品结构与效率解释。 |
| 17 | 自由现金流、资本开支、云基础设施投入和同比比较基数。 |

中文 `statement` 是对官方英文的忠实转述；`quoted_text` 只保留核验所需的极短原文或表格单元，完整语境通过页码和章节定位回到原始 PDF。只有真实财务表格的单元格使用 `quotation_mode = table_value` 且 locator 为 `table`；页面叙述使用 `exact_quote`。复合 statement 由多条 Citation 分别覆盖，不用人工拼接的分号字符串冒充原文。

这些引用由 golden-case curator 对上述两份、上述 SHA-256 的 PDF 做过逐页人工核验；同时用 PDF 文本抽取复查了全部 `exact_quote` 均能在指定 1-based 页中找到。清单固定全部 38 条引用规范化文本的 SHA-256，后续任何文字漂移都会令回放失败；citation adversarial suite 也会主动伪造一条不存在的引用，确认该攻击被捕获。因为仓库不再分发两份官方 PDF，golden case 的“首次确实存在于原文”仍来自这次 curator/manual verification；本地 `thesisos://` 快照则会在运行时直接解析并自动核对定位范围内的原文。

## 对象链

`examples/alibaba-2024-replay/` 保存以下对象：

1. `source-base-2024-02-07.json`：基准 SourceDocument。
2. `source-new-2024-05-14.json`：新 SourceDocument。
3. `evidence-base.json`：8 条基准 Evidence，包括 5 条原始事实与 3 条管理层观点。
4. `evidence-new.json`：12 条新 Evidence，包括 9 条原始事实与 3 条管理层解释，覆盖淘天、云、AIDC、自由现金流与回购。
5. `thesis-v1-confirmed.json`：4 条假设的用户确认基准版本。
6. `thesis-diff-pending.json`：逐条映射 4 条假设、包含待审完整 V2 的 ThesisDiff。
7. `user-review.json`：golden fixture 的显式 `accept` 记录。
8. `thesis-v2-confirmed.json`：接受后版本；内容与 proposal 相同，只按审阅边界更新确认元数据。

`evals/historical-replay/alibaba-2024-q4/case.json` 是机器入口。所有路径都相对 `case.json`；`documents` 与 `evidence` 是路径数组，其余对象路径是单个字符串。清单用 `base_analysis_cutoff_at`、`base_source_document_ids` 和 `base_evidence_ids` 锁定 V1 的历史信息集，并用 `expected_assumption_evidence_ids` 固定每条假设的 curator-approved 证据集合，防止把真实证据交换到错误假设上仍获得通过。清单还标注了 12 条关键财务事实、20 条关键证据和 38 条人工核验引用文本锚，防止通过漏标或改写不方便的事实来虚增覆盖率。

数值、订单、会员、货币化率和分部 EBITA 等披露结果属于 `source_fact/source_document`；“下降主要因为何种投入或业务组合”等公司给出的因果解释被拆成独立的 `source_opinion/management`。Diff 可同时引用二者，但不会把管理层解释伪装成已经独立验证的事实。

## 数据判断

### 基准假设

V1 只有 4 条核心假设，分别检验：

1. 淘天用户参与度能否转化为变现和分部利润；
2. 云从项目制收入转向公共云与 AI 后能否同时改善收入质量、增长与盈利；
3. AIDC 高增长能否产生单位经济性和亏损收窄；
4. 现金流能否同时支持核心投入和真正降低稀释后股数的回购。

每条假设均双向链接到指标和证伪条件。最强反方直接攻击这 4 条假设，不使用“竞争激烈”等通用模板。

### Diff 结论

总体结论为 `slightly_strengthened`，而不是明显增强：

- 淘天假设 `slightly_strengthened`：GMV、订单、购买人数、88VIP 与客户管理收入构成正面证据，但货币化率下降、调整后 EBITA 同比下降限制了结论强度。
- 云假设 `slightly_strengthened`：核心公共云产品增长和调整后 EBITA 增长支持收入质量改善，但剔除并表子公司的收入仍略降。
- AIDC 假设 `slightly_weakened`：收入与订单高增长没有在本期转化为亏损收窄，调整后 EBITA 亏损反而扩大。
- 资本配置假设 `unchanged`：回购带来净股数下降，但自由现金流受云基础设施资本开支及同比比较基数影响而大幅下降，正负证据抵消。

这是一个刻意混合的结论：同一指标变好不会自动使投资逻辑增强。Diff 同时保存了替代解释和一条针对性反方——增长可能由更低货币化、更大分部亏损和更高资本投入换来。

### 高信息量问题

Diff 只保留 3 个能改变判断的问题：

1. 客户管理收入能否连续快于 GMV，证明淘天货币化率企稳；
2. 剔除并表子公司后，公共云与 AI 能否令外部客户收入恢复增长并保持利润改善；
3. AIDC 的每单履约亏损能否随 Choice 订单规模下降。

每个问题都注明所需证据和它将如何改变相应假设，不是通用的“风险是什么”模板。

## 审阅与版本不变量

- V1 的 `version_id` 为 `baba-thesis-v1-2024-02-07`，`user_confirmed` 为 `true`，且 `supersedes` 为 `null`。
- proposal 的 `version_id` 为 `baba-thesis-v2-2024-05-15`，`supersedes` 指向 V1，`user_confirmed` 必须为 `false`。
- UserReview 指向同一个 Diff、公司、Thesis 和基准版本，决策为 `accept`。
- 接受后的 V2 使用 proposal 的相同新版本 ID，`user_confirmed` 变为 `true`，`updated_at` 等于 `reviewed_at`。
- V1、Diff、Review 和 V2 都是独立对象；接受更新不覆盖 V1。

fixture 中的 reviewer 是评测用 curator 身份，只表示预先审定的期望链条，不代表真实账户操作或投资交易。

## 机器校验

从仓库根目录运行：

```console
./.venv/bin/python -m thesisos eval-replay \
  evals/historical-replay/alibaba-2024-q4/case.json
```

golden case 应通过 18 项检查，其中包括：

- 未来信息泄漏为 0；
- 基准来源、证据、V1 创建时间及 V1 内全部证据引用均不越过基准截点；
- 关键财务事实来源覆盖为 12/12；
- 关键证据可追溯为 20/20；
- 4 条基准假设恰好各评估一次；
- 4 条假设使用的证据集合与 golden mapping 逐项完全相等；
- page/table locator 不超过 SourceDocument 的 `page_count`；
- 所有 Evidence 已明确验证，38 条引用文字与 curator 锚逐项一致；
- proposal 保持待审状态；
- 反方针对具体假设；
- 只有 1-3 个高信息量问题；
- 机器字段和生成文本中都没有 V0 禁止的交易指令、评级、目标价或仓位建议；
- `accept` 只产生预期的不可变确认版本。

完整领域模型校验还会检查 schema 版本、枚举、稳定 ID、引用快照、正反向假设链接、时间感知时间戳、证据可用时间、来源集合和审阅身份链。安装项目依赖后，可另外用 CLI 对单个 canonical 对象执行 Draft 2020-12 Schema 校验。

## 已知限制

- 回放不保存或再分发官方 PDF，只保留 URL 与真实下载快照哈希；离线逐字复核需要使用相同 URL 重新取得文件并核对 SHA-256。
- `publicly_available_at` 是对 PDF 内嵌生成时间向上取整后的保守 fixture 时间，不是经官方证明的精确上线秒数。
- V0 没有估值所需的截止日市场价格和完整调整数据，因此两个 Thesis 版本都明确使用 `valuation_anchor.status = insufficient_evidence`，不编造估值区间。
- 本例验证的是研究状态变更纪律，不验证后续股价，也不利用事后结果给 2024 年 5 月的结论打分。
