# SWE-bench Job Analysis — 设计说明

Version: Harbor-local v2.2

> **操作指南**（安装、选配置、运行命令）见 [README.md](README.md) 和 [configs/README.md](configs/README.md)。  
> 本文档聚焦**设计意图、特征语义、标签体系与工程边界**。

---

## 目录

1. [背景](#1-背景)
2. [设计目标](#2-设计目标)
3. [输入模型](#3-输入模型)
4. [Pipeline 阶段](#4-pipeline-阶段)
5. [模块职责](#5-模块职责)
6. [轨迹与补丁重建](#6-轨迹与补丁重建)
7. [确定性特征](#7-确定性特征)
8. [Tier-1 Judge](#8-tier-1-judge)
9. [多轴标签与归因](#9-多轴标签与归因)
10. [Reward Hack 检测边界](#10-reward-hack-检测边界)
11. [Task Analysis](#11-task-analysis)
12. [Traj Analysis（TQS）](#12-traj-analysis-tqs)
13. [Instance Analysis](#13-instance-analysis)
14. [Output Schema](#14-output-schema)
15. [Gold-aware 与 Online 信号分流](#15-gold-aware-与-online-信号分流)
16. [MVP 边界](#16-mvp-边界)
17. [验收标准](#17-验收标准)
18. [风险与缓解](#18-风险与缓解)

---

## 1. 背景

Resolved rate / pass@k 适合横向比较，但不能回答：

- 未解决实例集中在哪些 repo、语言、难度、bug 类型？resolved 与 failed 差异在哪？
- 根因是定位、诊断、实现、工具使用、长程规划，还是环境 / reward / 评测口径？
- 下一轮 SFT、RL、prompt 或数据应优先补哪里？

Harbor 评测产物（job 目录、trial 结果、LiteLLM 轨迹、verifier report、dataset metadata）经本 pipeline 归一化为结构化分析表。

## 2. 设计目标

**主目标**：评测产物 → 多轴标签 → 分布报告 → 可执行改进假设。

| 编号 | 目标 |
| --- | --- |
| G1 | 覆盖 failed + 可选 resolved 实例 |
| G2 | 五能力轴：localization / diagnosis / implementation / tool_usage / long_horizon |
| G3 | failed vs resolved 可比较 |
| G4 | 高频瓶颈模式可映射到数据 / reward / agent loop 动作 |
| G5 | 支持 OpenHands JSONL 与 Harbor job 两种输入 |

**非目标**：Tier-2 镜像复现、gold-aware 作 online RL reward、单一 final_category。

## 3. 输入模型

### 3.1 OpenHands JSONL

`trajectory_layout: openhands_jsonl`（默认）

| 文件 | 作用 |
| --- | --- |
| `trajectory_file` | 轨迹 + model patch |
| `test_file` | SWE-bench gold |
| `report_file` | completed / resolved / error / empty_patch IDs |

`traj_analysis` 开启时，trajectory 每行需含 `history`。

### 3.2 Harbor Job

`trajectory_layout: harbor_job`

| 路径 | 作用 |
| --- | --- |
| `<trial>/agent/litellm-trajectory.jsonl` | LiteLLM 轨迹 |
| `<trial>/result.json` | trial 元信息、reward |
| `<trial>/verifier/report.json` | resolved |
| `<dataset>/<id>/tests/config.json` | gold patch、tests |
| `<dataset>/<id>/metadata.json` | 难度、tags（instance_analysis） |

## 4. Pipeline 阶段

```text
Stage 0  加载配置、解析路径
Stage 1  轨迹解析 + model patch 重建
Stage 2  加载 gold + 解析 gold/model diff
Stage 3  确定性特征（C1–C5、病理、hack）
Stage 4  Tier-1 Judge（默认启发式）
Stage 5  五轴标签 + 归因
Stage 6  聚合报告
Stage 7  可选 task_analysis
Stage 8  可选 traj_analysis
Stage 9  可选 instance_analysis
```

## 5. 模块职责

| Module | Input | Output | LLM |
| --- | --- | --- | --- |
| `parser.trajectory_parser` | OpenHands / LiteLLM | `Trajectory` | No |
| `parser.patch_parser` | unified diff | `PatchInfo` | No |
| `parser.test_parser` | JSONL / Harbor dataset | `GoldInstance` | No |
| `features.localization` | trajectory + patches | C1–C5, diff stats | No |
| `features.pathology` | trajectory | loop, stop, storm, truncation | No |
| `hack_detector.reward_hack` | model patch | H1/H4/H5 | No |
| `judge.tier1_judge` | trajectory + features | structured judgment | Optional |
| `labeler.axis_labeler` | features + judgment | axes, attribution, verdict | No |
| `aggregator.report` | per-instance records | distributions, comparison | No |
| `task_analysis` | gold + problem | difficulty, domain, bug type | No |
| `instance_analysis` | instances + metadata | contingency, correlations | No |
| `traj_analysis` | trajectories / history | TQS scores | No |

## 6. 轨迹与补丁重建

Model patch 来源（优先级）：

1. LiteLLM file-editor 全状态 diff（`_FileStateTracker`）
2. 逐 edit 合成的 snippet diff（hybrid fallback）
3. OpenHands JSONL 中显式 `git_patch`

注意：路径归一化（`/testbed/` 等）、多 hunk 不合并、`undo_edit` 回放。见 `tests/test_patch_reconstruction.py`。

## 7. 确定性特征

### 7.1 Localization C1–C5

| 检查点 | 含义 | Gold-aware |
| --- | --- | --- |
| C1 | 是否查看 gold 涉及文件 | Yes |
| C2 | 是否查看 gold 涉及函数/类 | Yes |
| C3 | model patch 是否修改 gold 涉及文件 | Yes |
| C4 | model patch 是否修改 gold 涉及函数/类 | Yes |
| C5 | 是否执行测试命令 | No |

C3/C4 只表示 alignment，不表示 correctness。

### 7.2 Diff & 病理

| 特征 | 含义 |
| --- | --- |
| `diff_hunk_overlap` | hunk 重叠率 |
| `diff_line_delta` / `diff_file_delta` | 行数/文件数差异 |
| `loop_detected` | 连续重复操作（默认阈值 3） |
| `premature_stop` | 剩余迭代 >30% 时结束 |
| `tool_error_storm` | 连续工具错误（默认 5） |
| `context_truncation` | 疑似上下文截断 |

## 8. Tier-1 Judge

默认 `judge.enabled: false`，走启发式回退，不依赖外部 API。

启用后输出：`localization_quality`、`behavioral_understanding`、`implementation_quality`、`tool_usage_health`、`failure_attribution`、`reasoning`、`evidence_step_ids`。

Resolved 实例的 prompt 与启发式逻辑与 failed 区分处理。

## 9. 多轴标签与归因

### 五轴

| 轴 | 取值 |
| --- | --- |
| localization | `hit`, `partial`, `miss` |
| diagnosis | `correct`, `partial`, `wrong` |
| implementation | `correct`, `partial_fix`, `wrong_logic`, `wrong_location`, `no_edit` |
| tool_usage | `ok`, `suboptimal`, `poor` |
| long_horizon | `ok`, `premature_stop`, `truncation`, `max_iter_reached` |

### Flags

| Flag | 触发条件 |
| --- | --- |
| `hack` | H1/H4/H5 检测到 |
| `environment_noise` | 有 trajectory error 但仍有 patch |
| `missing_gold` | gold 数据未加载 |
| `empty_patch` | 空 patch |
| `error_instance` | error 且无 patch |
| `alternative_fix` | resolved 且 hunk overlap < 0.5 |

### 归因优先级

```text
hack > environment > missing_gold > empty_patch > error
> localization > diagnosis > implementation > tool_usage > long_horizon
```

### Correctness Verdict

| Verdict | 含义 |
| --- | --- |
| V1 | 正确修复 |
| V2 | 等价替代修复（`alternative_fix`） |
| V3 | 部分正确 |
| V4 | 错误修复 |
| V5 | 无有效尝试 |

## 10. Reward Hack 检测边界

MVP 覆盖测试侧：H1（改断言/skip）、H4（改 conftest/pytest.ini）、H5（setup 注入预期值）。

对于 SWE-Bench image-based 任务，D 类「读取 Git 历史 / git log / 语义等价历史信息」hack 默认忽略：SWE-Bench 镜像中的本地仓库已经删除了 git log 相关信息，不能稳定构成可利用的本地历史泄漏信号。若未来某个任务镜像重新保留 `.git` 历史或提供等价历史上下文，再单独升级为疑似或确认信号。

**不应**仅凭「通过但不在 gold hunk」判定 hack——可能是 valid alternative fix。

## 11. Task Analysis

输出 patch 规模、测试复杂度、problem 特征、difficulty tier、bug type、domain。比较 failed vs resolved 在各维度上的分布与解决率。

## 12. Traj Analysis（TQS）

内置 TQS V2 规则分，比较 resolved vs unresolved 轨迹质量。实现位于 `src/traj_analysis/`（含 `jsonl_io.py`），**不依赖**外部 `swe_data_process`。

输出：`traj_analysis/resolved_im.jsonl`、`unresolved_im.jsonl`、`score_comparison.json`、`report_comparison.txt`。

## 13. Instance Analysis

Join `instances.jsonl` 与 `<dataset>/<id>/metadata.json`，输出交叉表与 Pearson/Spearman 相关性。依赖 `scipy`。

## 14. Output Schema

```json
{
  "instance_id": "django__django-10097",
  "resolved": false,
  "scaffold": "openhands-sdk",
  "model": "Qwen3.5-35B-A3B",
  "taxonomy_version": "v1",
  "judge_version": "v1",
  "trajectory_metadata": {
    "model_patch_source": "reconstructed_from_file_editor_full_state",
    "model_patch_edit_count": 3,
    "model_patch_files": ["pkg/mod.py"]
  },
  "deterministic_features": { "C1_file_read": true, "diff_hunk_overlap": 0.5 },
  "axes": { "localization": "partial", "implementation": "wrong_logic" },
  "primary_failure": "implementation",
  "secondary_failures": ["diagnosis"],
  "flags": [],
  "correctness_verdict": "V4"
}
```

## 15. Gold-aware 与 Online 信号分流

| 类型 | 示例 | 允许用途 |
| --- | --- | --- |
| Gold-aware offline | C1–C4, hunk overlap, judge+gold | 离线归因、SFT 筛选、RM 标签 |
| Gold-free online | C5, tool errors, loop/storm, patch 可应用性 | online reward、agent loop |

**禁止**将 gold-aware 信号直接用作 online RL step reward。

## 16. MVP 边界

**已覆盖**：双输入布局、patch 重建、确定性特征、启发式 judge、五轴标签、failed/resolved 报告、task/instance/traj 可选分析。

**延后**：Tier-2 复现、源码侧 H6–H10、judge 校准、kappa 报告、dashboard、counterfactual 分析。

## 17. 验收标准

- `instances.jsonl` 覆盖有轨迹的目标 instance
- failed/resolved 报告与 `score_comparison.txt` 可生成
- 可选阶段在启用时各自产出预期文件
- `pytest tests/` 通过

## 18. 风险与缓解

| Risk | Mitigation |
| --- | --- |
| Patch 重建误差影响 C3/C4 | `trajectory_metadata` 可观测 + 单测 + 抽样核对 |
| Judge 过粗 | 结构化 schema + 抽样校准 |
| Gold-aware 误用于 RL | 文档与命名明确 offline/online 边界 |
| Metadata 缺失 | `summary.json` 记录 missing 数 |
| TQS 口径漂移 | 评分代码保留在本仓库内 |
