# ThesisOS 开发进度与交接

最后更新：2026-08-25（Asia/Shanghai）

## 当前结论

README 定义的 ThesisDiff V0 最小可信闭环已经实现并可从命令行运行：

```text
SourceDocument
  → Evidence
  → confirmed ThesisCard
  → pending ThesisDiff
  → explicit UserReview
  → immutable new ThesisVersion
```

AI 不能把自己的输出标为已验证 Evidence，也不能静默晋升或覆盖用户确认的 ThesisCard。V0 不生成买卖、仓位、时点、目标价或交易信号。

正确仓库为 `https://github.com/yekwennnn/ThesisOS.git`，本轮起始提交为 `2d578eee75bb6d467c22e4fee7149c0b6a2367e9`。早期误取的同名第三方仓库已完整隔离到 `/private/tmp/ThesisOS-wrong-upstream-7fad93b-20260825`，其中代码没有混入本仓库。

## 本轮完成内容

### 1. 数据契约与判断边界

- 完成 SourceDocument、Citation、Evidence、ThesisCard、ThesisDiff、UserReview 六份 Draft 2020-12 JSON Schema。
- 完成与 Schema 对应的冻结 Python 模型、稳定枚举、严格未知字段拒绝和跨对象校验。
- 强制每张 ThesisCard 有 3–7 条假设、指标、证伪条件、最强反方、估值证据状态、未知问题和完整版本元数据。
- 强制每份 ThesisDiff 恰好覆盖全部基准假设，保存替代解释、管理层 say/do 比较、定向反方、1–3 个高信息量问题和完整待审 ThesisCard。
- 对完整拟议 Thesis 与基准逐 stable ID 对账：每个实际新增、修改、删除都必须有 operation/target 完全一致的 ChangeItem；错误 ID、聚合目标、重复目标、decoy `keep` 和无显式操作的重排都会 fail closed。
- 区分 `source_fact`、`source_opinion`、`user_judgment`、`ai_inference`；证据不足是合法结果，不能用推断补空白。
- 用本地策略扫描所有 AI 可写自由文本（含 tags 与指标单位/定义），阻止 V0 生成交易动作、评级、仓位和目标价字段或文本。

### 2. 来源、快照与可执行引用

- 支持手工导入 PDF、Markdown 和 UTF-8 纯文本；原始字节进入 SHA-256 内容寻址对象库，不做转码或改写。
- 下游保存 Evidence、生成 Diff 和审阅时都会重新校验 managed snapshot 的哈希和字节数。
- PDF 由 `pypdf` 解析为物理页；文本视图使用稳定的 1-based 页、行和段落定位。
- exact quote 在限定 locator 内核验；faithful paraphrase 永远要求人工语义确认。
- `table_value` 只有在表题唯一、表头位于数据行之前、表头/数据行具有相同 pipe/tab 列结构、行名精确匹配首列且列名唯一时，才映射并核验一个完整单元格。规范化 quote 必须与整格相等，不能漏掉负号、单位或百分号；表后正文、伪列名和非结构化布局均 fail closed。
- 防止路径逃逸、符号链接绕过、managed URI 大小写绕过、快照替换和同哈希不同内容。

### 3. 模型 adapter 与 admission gate

- 提供 provider-neutral、`shell=False` 的 subprocess adapter 协议；stdin 为版本化 JSON envelope，stdout 只能是一个受限 JSON 值。
- 设置 timeout、stdout 上限、UTF-8/JSON/重复键/输出形状门禁；argv 不写入 provenance 或错误信息。
- request metadata 使用精确 allowlist 和类型校验，未知字段、嵌套对象及疑似凭据不会传给 adapter 或落盘。
- Evidence extraction 只能产出 `unreviewed`；只有人工检查并调用 `save-evidence` 后，才能成为可用的 verified Evidence。
- ThesisDiff generation 在调用前检查当前 Thesis、来源、Evidence、managed 引用和历史 cutoff；调用后再次检查 Schema、身份、时间、证据角色、假设覆盖和策略边界。
- `past_evidence_ids` 必须来自显式 `--prior-evidence`；所有当前材料角色必须来自显式 `--evidence`。拟议 Thesis 新增的 Evidence 引用也受同一门禁。
- 基准 Thesis 已有的反方 Evidence 只作为完整性依赖自动解析，不加入模型上下文，并按基准版本自己的时间边界复核，不能用后来证据回填过去判断。
- 每个 company/run-id 有跨进程 reservation，覆盖“查重 → adapter → admission → publication”；并发复用同一 run-id 实测只调用 adapter 一次。
- prompt、标准化输入/输出、模型标识、对象引用和运行时间都有哈希化 provenance；ThesisDiff prompt contract 已更新到 `0.1.2`。

### 4. 人工审阅、不可变存储与并发

- 工作区保存不可变 documents、evidence、diffs、reviews、research tasks、model runs 和 thesis versions。
- 正式版本必须 `user_confirmed=true`；AI proposal 必须为 `false`。只有显式 `accept` 或带完整用户编辑版本的 `accept_with_edits` 才能晋升。
- 版本链只允许 supersede 当前版本；过期 Diff、身份漂移、同 ID 异内容、指针哈希不一致都会被拒绝。
- 正式版本不能把 `as_of_date`、`created_at` 或 `updated_at` 倒退到当前确认版本之前；正式提交和 `accept_with_edits` 还会复核完整 Evidence 依赖。
- 公司级跨进程锁线性化提交与审阅；当前指针带记录 SHA-256 并原子替换。
- JSONL 审计日志使用独立全局锁和 append/fsync。正式版本提交、单工件保存、人工审阅和 model-run + Diff bundle 都在发布首条状态前锁定、打开并验证审计目标；审计路径故障时不会留下无日志版本、工件或半份审阅。
- 普通验证、目录、审计目标和并发冲突不会留下半个 model-run/Diff bundle 或审阅；工作区工件枚举逐文件拒绝符号链接并核对路径 ID。

### 5. CLI、打包与 CI

- CLI 已覆盖 `init`、`status`、`snapshot-info`、`validate`、`ingest-document`、`save-document`、`save-evidence`、`extract-evidence`、`generate-diff`、`review`、`eval-replay`、`eval-suite`。
- 所有命令输出机器可读 JSON；用户输入/领域/评测失败返回 2，意外 I/O 或内部故障返回 1，均不输出 traceback。
- 项目可构建 wheel/sdist；6 份 Schema 和 4 份 Prompt 作为包资源分发，从仓库外 cwd 和 `pip --target` 安装均可加载。
- GitHub Actions 配置 Linux Python 3.10/3.12/3.14 和 Windows Python 3.10/3.14，先构建并安装 wheel，再从 clean cwd 检查资源并运行测试。

### 6. 真实历史回放与攻击评测

- 完成阿里巴巴 2024-02-07 基准公告 → 2024-05-14 新公告的真实历史回放。
- 保存两份官方 PDF 的 URL、页数、字节数和 SHA-256；不把官方 PDF 重新分发进仓库。
- golden case 固定 4 条假设、12 条关键财务事实、20 条关键 Evidence 和 38 条人工核验引用文本锚。
- 回放通过 18/18 项检查；关键财务事实来源覆盖 12/12，关键 Evidence 可追溯 20/20，未来信息泄漏为 0。
- citation accuracy、assumption mapping、future leakage 三套独立 adversarial suite 全部通过，7/7 个声明攻击均被预期门禁捕获。

## 本地验证记录

2026-08-25 最新工作树验证：

- 源码 `python -m unittest discover -s tests -v`：171/171 通过。
- 最新 wheel 普通安装后、不设置 `PYTHONPATH` 的同一套测试：171/171 通过。
- 从仓库外 cwd 加载包内资源：6/6 Schema，ThesisDiff prompt contract `0.1.2`。
- 最新工作树 wheel：`thesisos-0.1.0-py3-none-any.whl`，SHA-256 `868e5c102ebf40d799a0a70dc4b6fc1446bd220072e17b97ae02f9743cffea92`。
- 功能提交 `9a533e3` 的干净 Git archive：171/171 通过；从该 archive 构建的 wheel SHA-256 为 `441b14eefdec764d202340623f8feb7eedcaf410b771f996861bc88a58dd510e`，仓库外资源 smoke test 通过。
- Alibaba replay：18/18 检查通过。
- adversarial suites：3/3 suite、7/7 mutation 通过。
- `compileall`、`git diff --check`、冲突标记扫描通过。
- wheel、sdist、由 sdist 重建 wheel、普通安装、`pip --target` 安装、仓库外资源加载和 Git archive 重建均已通过。

## 尚未完成 / 已知限制

- 没有内置任何厂商 SDK、API key 或默认模型 adapter；测试使用确定性假 adapter，尚未验证真实模型的输出质量、成本和稳定性。
- 目前只有一个阿里巴巴真实 replay；README 首批试点中的京东、美团以及更多报告期尚未建立 golden case。
- 官方 PDF 因版权和体积不入库；外部 replay 的 38 条文字锚依赖 curator 对指定 SHA-256 的人工核验。只有本地 managed snapshot 能在运行时自动逐字复核。
- 外部 `https://` / `file://` URI 是元数据兼容模式，不会由工作区重新下载或验证字节。
- 扫描型 PDF/OCR、复杂跨页表格、合并单元格和非 pipe/tab 文本表格没有自动核验；不确定布局会转人工，而不是猜测。
- 当前是本地单用户 CLI，没有 Web 客户端、账户认证或多人权限系统。“UserReview 来自真实已认证人类”在 V0 由调用方负责。
- 文件型 bundle 能防普通失败，但突然断电仍不是跨多个文件的数据库事务；生产化需要 journal/SQLite 和启动恢复流程。
- README 的“15 分钟完成判断”、真实用户价值、留存和付费意愿尚未经过用户实验验证。
- 尚未选择 LICENSE，`pyproject.toml` 也没有 license metadata；在用户决定许可证前，不应把当前包视为可公开分发版本。
- CI workflow 已配置并完成本地等价检查，但尚未在本轮提交后的 GitHub Actions 上实际运行。
- 本机 `gh auth status` 显示现有 GitHub 凭据失效；同时，直接推送默认 `main` 属于未单独授权的外部发布，安全审批门禁已拒绝本轮 `git push`。当前成果已在本地提交，用户重新登录并明确授权远程发布后再同步。

## 下一步建议

1. 用户选择许可证并补 `LICENSE` / package metadata。
2. 接入一个真实、受控的模型 adapter，记录成本、延迟、失败率和输出 admission 通过率。
3. 增加京东、美团及至少两个后续报告期 replay，防止对单一公司/文档风格过拟合。
4. 用真实用户验证 15 分钟承诺和 Diff 的决策信息价值。
5. 若进入多人或长期生产使用，迁移到带事务、恢复、身份和权限边界的存储/服务层。

## 恢复入口

从仓库根目录执行：

```console
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m thesisos eval-replay \
  evals/historical-replay/alibaba-2024-q4/case.json
./.venv/bin/python -m thesisos eval-suite \
  evals/citation-accuracy/suite.json \
  evals/assumption-mapping/suite.json \
  evals/future-leakage/suite.json
```

关键说明入口：

- `README.md`：产品范围、原则和运行入口。
- `docs/data-contracts.md`：对象与跨对象不变量。
- `docs/model-adapter-protocol.md`：adapter transport、输入角色和 admission gate。
- `docs/workspace-format.md`：本地存储、并发、审计与 crash boundary。
- `docs/historical-replay-alibaba-2024.md`：真实回放的来源、时间边界和结论。
- 本文档：已完成工作、验证记录、限制与下一步。
