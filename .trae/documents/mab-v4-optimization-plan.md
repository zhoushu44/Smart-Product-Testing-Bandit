# MAB v4 优化实施计划

## Context

当前多臂老虎机测款系统（v3）基于 Beta-Bernoulli 后验 + 硬编码四层 if-else 淘汰，存在以下核心问题：

1. **伯努利假设丢失幅度** — 花10元达标 vs 花1000元达标在模型里等价，而实际数据85%天-商品组合为0成交，仅15%有GMV
2. **无时间衰减** — 日ROI从0.15到3.36剧烈波动，但7天数据等权聚合
3. **淘汰逻辑不自适应** — 硬编码if-else，不基于统计检验
4. **预算分配忽略方差** — softmax(TS) 不考虑各臂波动性差异
5. **两版本算法不一致** — JS四层淘汰 vs Python单条件淘汰
6. **忽略净ROI** — 有6%差异但未使用

基于 NeurIPS 2024 (CR)、UAI 2023 (SHAdaVar)、ICML 2025 等顶会论文，进行6项优化。

## 修改文件

- `index.html` — 前端JS版，~649行 → ~850行
- `bandit_test.py` — Python CLI版，~385行 → ~500行

## 实施步骤

### Step 1: 高斯后验 (Normal-Inverse-Gamma) 替代 Beta-Bernoulli

**数学公式**:
- 模型: daily_ROI ~ N(μ, σ²)
- 先验: μ|σ² ~ N(0, σ²/0.01), σ² ~ Inv-Gamma(0.01, 0.01)
- 后验更新: κ_n = κ_0 + n_eff, μ_n = (κ_0·μ_0 + n_eff·x̄)/κ_n, a_n = a_0 + n_eff/2, b_n = b_0 + S/2 + κ_0·n_eff·(x̄-μ_0)²/(2·κ_n)
- P(ROI≥target) = 1 - CDF_t((μ_n - target)/scale, df=2a_n)
- 95% CI: μ_n ± t_{0.975, df} · scale

**index.html 改动**:
- 新增 `nigPosterior(observations, target, nSamples, seed)` 函数
- 新增 `studentTCDF(t, df)` — 用正则不完全Beta函数近似（手写连续分数算法）
- 新增 `sampleNigPosterior(muN, kappaN, aN, bN, seed)` — 复用现有 gammaSample/normalSample
- 保留 `betaCI` 作为降级模式（`useGaussian=false` 时走原路径）

**bandit_test.py 改动**:
- 新增 `nig_posterior(observations, target_roi, n_samples, seed)` — 用 scipy.stats.t 解析解
- evaluate 函数根据 `use_gaussian` 参数切换 NIG/Beta

### Step 2: 时间衰减 + 非平稳检测

**公式**: w(t) = exp(-λ · days_ago), λ默认0.15（约7天半衰期）

**全局漂移检测**:
- 最近3天 vs 之前天数的加权平均ROI差 > 0.5 则判定漂移
- 漂移时用残差ROI = 原始ROI - 全局均值 + 目标值 做后验推断

**趋势检测**: 对最近3个有花费天做线性回归，斜率>0.1=上升，<-0.1=下降

**index.html 改动**:
- `mapCols` 新增 netGmv、netDeals 列映射
- `aggregate` 中 daily[dt] 新增 netGmv、netDeals 字段
- `aggregate` 返回值新增 dailyRois[]、trendSlope、trendLabel、dailyVar、netGmv、netRoi
- 新增 `parseDate(str)` 辅助函数
- 新增 `detectGlobalShift(allDailyRois, decayRate)` 函数
- evaluate 中构建带权观测值传入 nigPosterior

**bandit_test.py 改动**:
- `aggregate_by_product` 新增日净ROI列、趋势斜率/标签、日ROI方差
- 新增 `detect_global_shift(daily_roi_df, decay_rate)` 函数
- evaluate 接收 daily_roi_df 参数，构建带权观测值

### Step 3: CR 连续淘汰替代 if-else

**公式**: threshold_i = sqrt(2·log(4·n_eff_i²/δ) / n_eff_i)
- 若 gap = max_j(postHi_j) - postLo_i > threshold_i → 淘汰
- 保留两个特殊规则：0展现直接淘汰、0成交直接淘汰

**index.html 改动**:
- evaluate 函数中替换四层 if-else 链为 CR 判定逻辑
- 主推判定改为：pTarget >= 0.5 且 postMean >= targetRoi × 0.6

**bandit_test.py 改动**:
- 新增 `decide_cr(row, all_rows, delta)` 替换原 `decide()`
- 逻辑与 JS 版完全对齐

### Step 4: 方差自适应预算分配

**公式**: budget_weight_i = exp(temp · ts_i) · sqrt(max(postVar_i, ε))
- 替代原来的 exp(temp · ts_i)

**两版本同步修改**:
- evaluate 中预算分配逻辑从 softmax(TS) 改为 softmax(TS)·sqrt(var)
- cap-and-redistribute 迭代逻辑不变

### Step 5: 净 ROI 集成

**index.html**: aggregate 中计算 netGmv、netDeals、netRoi，详情面板和简表展示
**bandit_test.py**: 已有净ROI字段，确保传入 NIG 后验（用净ROI替代毛ROI做建模）

### Step 6: UI 更新 + 版本统一

**index.html 新增UI**:
- 高级参数区新增：时间衰减率λ、淘汰显著性δ、后验模型选择（高斯/Beta）
- 简表新增列：趋势（↑上升/↓下降/→平稳）、净ROI
- 详情面板新增：净交易额、净成交、净ROI、趋势斜率、日ROI方差、P(ROI≥目标)、后验模型、环境漂移、有效样本量
- 统计行新增：净ROI、漂移检测状态
- 版本号 v3 → v4，副标题更新

**bandit_test.py**:
- argparse 新增 --decay-rate, --delta, --no-gaussian 参数
- 报告输出新增：净ROI、趋势、日ROI方差、后验均值、有效样本量、环境漂移
- 版本号 v4

## 验证方案

1. **Python 单元测试**: 对 nig_posterior 用已知数据验证 μ/pTarget 与 scipy 解析解对齐（误差<1e-3）
2. **前端手动验证**: 上传同一 Excel，对比 v3 Beta 结果与 v4 NIG 结果
3. **回归验证**: --no-gaussian 模式下结果应与 v3 完全一致
4. **10个0成交臂**: 全部应被 CR 淘汰（与旧版行为一致）
5. **Top3臂**: 预算分配中方差大的臂应略高于纯 TS 分配
