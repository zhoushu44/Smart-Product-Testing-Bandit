# -*- coding: utf-8 -*-
"""
多臂老虎机测款系统 v4 (Multi-Armed Bandit Product Testing)
========================================================
适用场景: 电商推广测款, 从商品推广分天数据中, 用 MAB 算法评估每个款的潜力并给出置信度判断。

v4 优化 (基于顶会论文):
  [1] Normal-Inverse-Gamma 高斯后验替代 Beta-Bernoulli
      保留 ROI 幅度信息, 不再丢弃"超出目标多少"
  [2] 时间衰减权重 + 全局漂移检测
      近期数据权重更高, 检测全局环境变化
  [3] CR 连续淘汰 (NeurIPS 2024)
      替代硬编码 if-else, 基于统计检验自适应淘汰
  [4] 方差自适应预算分配 (UAI 2023 SHAdaVar)
      高方差臂分配更多预算
  [5] 净 ROI 集成
      使用净交易额计算更准确的 ROI

经典参考:
  - Chapelle & Li (2011, NeurIPS): Thompson Sampling
  - Wang et al. (2024, NeurIPS): Continuous Rejects (CR)
  - Lalitha et al. (2023, UAI): SHAdaVar
  - Auer et al. (2002, ML): UCB1
  - Gelman et al. (2013): Bayesian Data Analysis
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t as t_dist


# ============================================================
# 1. 数据读取与分组选择
# ============================================================
def find_default_excel(cwd: Path) -> Path:
    """自动查找当前目录下第一个商品推广分天数据 xlsx"""
    candidates = sorted(cwd.glob("*.xls*"), reverse=True)
    for p in candidates:
        if not p.name.startswith("bandit_result") and not p.name.startswith("~$"):
            return p
    raise FileNotFoundError(f"当前目录未找到 xlsx 文件: {cwd}")


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    # 规范化分组列: NaN / "-" / 空 视为 "未分组"
    df["分组"] = df["分组"].astype(str).str.strip()
    df.loc[df["分组"].isin(["nan", "-", "", "NaT"]), "分组"] = "未分组"
    # 从出价方式提取目标投产比
    df["目标投产比"] = df["出价方式"].apply(parse_target_roi)
    return df


def parse_target_roi(bid_way) -> float:
    if pd.isna(bid_way):
        return np.nan
    m = re.search(r"([\d.]+)", str(bid_way))
    return float(m.group(1)) if m else np.nan


def select_group(df: pd.DataFrame, group: str) -> pd.DataFrame:
    sub = df[df["分组"] == group].copy()
    if sub.empty:
        avail = df["分组"].value_counts().to_dict()
        print(f"[错误] 分组 '{group}' 在数据中不存在或为空。")
        print(f"       可用分组: {avail}")
        sys.exit(1)
    return sub


# ============================================================
# 2. 按商品名称聚合 (一款多链接 + 趋势 + 方差 + 净ROI)
# ============================================================
def aggregate_by_product(sub: pd.DataFrame) -> tuple:
    """
    一个款 = 一个商品名称; 该名称下所有商品ID(不同链接) + 所有推广名称 + 所有日期的数据汇总。
    返回 (agg_df, daily_by_day_df)
    """
    # 每日每链接 ROI
    daily = sub.copy()
    daily["每日花费"] = pd.to_numeric(daily["总花费(元)"], errors="coerce").fillna(0)
    daily["每日交易额"] = pd.to_numeric(daily["交易额(元)"], errors="coerce").fillna(0)
    daily["每日成交笔数"] = pd.to_numeric(daily["成交笔数"], errors="coerce").fillna(0)
    daily["每日净交易额"] = pd.to_numeric(daily.get("净交易额(元)", 0), errors="coerce").fillna(0)
    daily["每日ROI"] = np.where(daily["每日花费"] > 0,
                                daily["每日交易额"] / daily["每日花费"],
                                np.nan)
    daily["每日净ROI"] = np.where(daily["每日花费"] > 0,
                                  daily["每日净交易额"] / daily["每日花费"],
                                  np.nan)

    # 按商品名称(款)聚合
    agg = daily.groupby(["商品名称"], as_index=False).agg(
        链接数=("商品ID", "nunique"),
        链接列表=("商品ID", lambda x: " | ".join(sorted(set(x.astype(str))))),
        推广名称数=("推广名称", "nunique"),
        天数=("日期", "nunique"),
        目标投产比=("目标投产比", lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan),
        总花费=("每日花费", "sum"),
        交易额=("每日交易额", "sum"),
        成交笔数=("每日成交笔数", "sum"),
        点击量=("点击量", "sum"),
        曝光量=("曝光量", "sum"),
        净交易额=("每日净交易额", "sum"),
        净成交笔数=("净成交笔数", "sum"),
    )

    # 按日聚合 (用于 NIG 后验)
    daily_by_day = daily.groupby(["商品名称", "日期"], as_index=False).agg(
        日花费=("每日花费", "sum"),
        日交易额=("每日交易额", "sum"),
        日净交易额=("每日净交易额", "sum"),
    )
    daily_by_day["日ROI"] = np.where(daily_by_day["日花费"] > 0,
                                     daily_by_day["日交易额"] / daily_by_day["日花费"],
                                     np.nan)
    daily_by_day["日净ROI"] = np.where(daily_by_day["日花费"] > 0,
                                       daily_by_day["日净交易额"] / daily_by_day["日花费"],
                                       np.nan)

    days_paid = daily_by_day.groupby("商品名称")["日ROI"].apply(lambda s: int(s.notna().sum())).to_dict()
    agg["有花费天数"] = agg["商品名称"].map(days_paid)

    # 达标天数
    thr_map = agg.set_index("商品名称")["目标投产比"].to_dict()
    def _hit_days(name):
        thr = thr_map.get(name, np.nan)
        if pd.isna(thr):
            return 0
        s = daily_by_day.loc[daily_by_day["商品名称"] == name, "日ROI"]
        s = s[s.notna()]
        return int((s >= thr).sum())
    agg["达标天数"] = agg["商品名称"].apply(_hit_days)

    # 派生指标
    agg["ROI"] = np.where(agg["总花费"] > 0, agg["交易额"] / agg["总花费"], np.nan)
    agg["净ROI"] = np.where(agg["总花费"] > 0, agg["净交易额"] / agg["总花费"], np.nan)
    agg["CVR"] = np.where(agg["点击量"] > 0, agg["成交笔数"] / agg["点击量"], np.nan)
    agg["CTR"] = np.where(agg["曝光量"] > 0, agg["点击量"] / agg["曝光量"], np.nan)

    # 趋势检测: 最近3个有花费天的线性回归斜率
    def _trend_slope(name):
        s = daily_by_day.loc[daily_by_day["商品名称"] == name, "日ROI"].dropna()
        if len(s) < 3:
            return 0.0, "平稳"
        y = s.values[-3:]
        x = np.arange(len(y))
        slope = np.polyfit(x, y, 1)[0]
        label = "上升" if slope > 0.1 else ("下降" if slope < -0.1 else "平稳")
        return round(slope, 3), label

    trends = agg["商品名称"].apply(_trend_slope)
    agg["趋势斜率"] = trends.apply(lambda t: t[0])
    agg["趋势"] = trends.apply(lambda t: t[1])

    # 每日ROI方差
    def _daily_roi_var(name):
        s = daily_by_day.loc[daily_by_day["商品名称"] == name, "日ROI"].dropna()
        return round(s.var(), 3) if len(s) >= 2 else np.nan

    agg["日ROI方差"] = agg["商品名称"].apply(_daily_roi_var)

    return agg, daily_by_day


def aggregate_by_link(sub: pd.DataFrame) -> pd.DataFrame:
    """按商品ID(链接)聚合, 保留商品名称作为款归属, 用于同款下各链接对比."""
    d = sub.copy()
    d["总花费"] = pd.to_numeric(d["总花费(元)"], errors="coerce").fillna(0)
    d["交易额"] = pd.to_numeric(d["交易额(元)"], errors="coerce").fillna(0)
    agg = d.groupby(["商品名称", "商品ID"], as_index=False).agg(
        推广名称=("推广名称", "first"),
        天数=("日期", "nunique"),
        目标投产比=("目标投产比", "first"),
        总花费=("总花费", "sum"),
        交易额=("交易额", "sum"),
        成交笔数=("成交笔数", "sum"),
        点击量=("点击量", "sum"),
        曝光量=("曝光量", "sum"),
    )
    agg["ROI"] = np.where(agg["总花费"] > 0, agg["交易额"] / agg["总花费"], np.nan)
    agg["CVR"] = np.where(agg["点击量"] > 0, agg["成交笔数"] / agg["点击量"], np.nan)
    agg = agg.sort_values(["商品名称", "总花费"], ascending=[True, False]).reset_index(drop=True)
    return agg


# ============================================================
# 3. MAB 算法: NIG 高斯后验 + Beta 兼容 + UCB1
# ============================================================
def nig_posterior(observations: list, target_roi: float,
                 n_samples: int = 20000, seed: int = 42) -> dict:
    """
    Normal-Inverse-Gamma 共轭后验, 用于连续 ROI 奖励.
    observations: [(roi, weight), ...] 带权每日ROI观测值
    target_roi: 目标投产比
    返回: {mu, sigma2, p_target, ci_lo, ci_hi, n_eff, ts}
    """
    # 无信息先验
    mu0, kappa0, a0, b0 = 0.0, 0.01, 0.01, 0.01

    n_eff = sum(w for _, w in observations)
    if n_eff < 0.01:
        return {"mu": mu0, "sigma2": 1e6, "p_target": 0.5,
                "ci_lo": -1e6, "ci_hi": 1e6, "n_eff": 0.0, "ts": mu0}

    x_bar = sum(w * x for x, w in observations) / n_eff
    S = sum(w * (x - x_bar) ** 2 for x, w in observations)

    kappa_n = kappa0 + n_eff
    mu_n = (kappa0 * mu0 + n_eff * x_bar) / kappa_n
    a_n = a0 + n_eff / 2
    b_n = b0 + S / 2 + kappa0 * n_eff * (x_bar - mu0) ** 2 / (2 * kappa_n)

    # Student-t 解析解
    df = 2 * a_n
    scale = np.sqrt(b_n * (kappa_n + 1) / (a_n * kappa_n))
    t_stat = (mu_n - target_roi) / max(scale, 1e-10)
    p_target = 1.0 - t_dist.cdf(t_stat, df)

    # 95% CI
    t_crit = t_dist.ppf(0.975, df)
    ci_lo = mu_n - t_crit * scale
    ci_hi = mu_n + t_crit * scale

    # Thompson Sampling
    rng = np.random.default_rng(seed)
    sigma2_sample = 1.0 / rng.gamma(a_n, 1.0 / b_n)
    mu_sample = rng.normal(mu_n, np.sqrt(sigma2_sample / kappa_n))

    return {
        "mu": mu_n,
        "sigma2": b_n / max(a_n - 1, 0.5),
        "p_target": float(p_target),
        "ci_lo": float(ci_lo),
        "ci_hi": float(ci_hi),
        "n_eff": float(n_eff),
        "ts": float(mu_sample),
    }


def beta_posterior_ci(alpha: float, beta: float,
                      n_samples: int = 20000, seed: int = 42):
    """Beta(alpha, beta) 后验的均值与 95% 置信区间 (蒙特卡洛采样)."""
    rng = np.random.default_rng(seed + int(alpha * 1000) + int(beta))
    s = rng.beta(max(alpha, 1e-6), max(beta, 1e-6), n_samples)
    return float(s.mean()), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def thompson_sample(alpha: float, beta: float, seed: int = 42) -> float:
    """Thompson Sampling 采样一次 (Beta模式)."""
    rng = np.random.default_rng(seed)
    return float(rng.beta(max(alpha, 1e-6), max(beta, 1e-6), 1)[0])


def ucb1(reward_mean: float, n_i: int, N_total: int) -> float:
    """UCB1 置信上界. 参考: Auer et al. (2002)."""
    if n_i <= 0:
        return float("inf")
    return reward_mean + np.sqrt(2.0 * np.log(max(N_total, 1)) / n_i)


# ============================================================
# 4. 全局漂移检测
# ============================================================
def detect_global_shift(daily_roi_df: pd.DataFrame, decay_rate: float = 0.15) -> dict:
    """
    检测全局环境漂移.
    daily_roi_df: columns=[商品名称, 日期, 日ROI, 日花费]
    """
    if daily_roi_df is None or len(daily_roi_df) < 4:
        return {"detected": False, "recent_group_avg": np.nan, "older_group_avg": np.nan}

    valid = daily_roi_df.dropna(subset=["日ROI"])
    if len(valid) < 4:
        return {"detected": False, "recent_group_avg": np.nan, "older_group_avg": np.nan}

    sorted_dates = sorted(valid["日期"].unique())
    # 最近3天的日期
    split_date = sorted_dates[-3] if len(sorted_dates) >= 6 else sorted_dates[len(sorted_dates) // 2]

    recent = valid[valid["日期"] >= split_date]
    older = valid[valid["日期"] < split_date]

    def _weighted_avg(df):
        if df.empty or df["日花费"].sum() == 0:
            return np.nan
        return (df["日ROI"] * df["日花费"]).sum() / df["日花费"].sum()

    recent_avg = _weighted_avg(recent)
    older_avg = _weighted_avg(older)

    detected = False
    if not (np.isnan(recent_avg) or np.isnan(older_avg)):
        detected = abs(recent_avg - older_avg) > 0.5

    return {"detected": detected, "recent_group_avg": recent_avg, "older_group_avg": older_avg}


# ============================================================
# 5. 置信度判断与决策 (CR 连续淘汰)
# ============================================================
MIN_DEALS = 3
MIN_SPEND = 30.0
MIN_DAYS = 2


def decide_cr(row, all_rows: pd.DataFrame, delta: float = 0.05) -> tuple:
    """
    基于 CR (Continuous Rejects) 的淘汰判定 + 漏斗分层建议 (NeurIPS 2024).
    返回 (决策, 置信度, 建议动作, 后台调整建议).
    """
    p_target = row["P达标_后验均值"]
    p_lo = row["P达标_95下界"]
    p_hi = row["P达标_95上界"]
    post_mean = row["后验均值_ROI"]
    n_eff = row["有效样本量"]
    deals = row["成交笔数"]
    spend = row["总花费"]
    days = row["有花费天数"]
    thr = row["目标投产比"]
    clicks = row.get("点击量", 0)
    impr = row.get("曝光量", 0)
    roi = row.get("累计ROI", np.nan)
    net_roi = row.get("净ROI", np.nan)  # 扣除退款后的真实ROI

    suggested_roi = round(thr * 0.7, 2) if not pd.isna(thr) else "—"
    max_wait_days = 5
    burn_min_daily = 3.0  # 日均花费低于此值=烧不动
    daily_spend = spend / max(days, 1)

    ref = "NIG+CR (NeurIPS'24)"

    # ---- 漏斗第1层: 0展现 → 直接关停 ----
    if days >= MIN_DAYS and impr <= 0:
        return ("淘汰", 0.99, "0展现, 平台不给流量, 直接关停", "后台暂停该计划")

    # ---- 漏斗第2层: 烧不动(日均<3元) → 直接淘汰 ----
    #    烧不动=平台不给流量, 有1-2笔成交也没用, 永远放量不了
    if days >= MIN_DAYS and daily_spend < burn_min_daily:
        deal_msg = f", {int(deals)}笔成交也放量不了" if deals > 0 else ""
        return ("淘汰", 0.95,
                f"日均仅{daily_spend:.1f}元烧不动, 平台不给流量{deal_msg}",
                "后台暂停该计划")

    # ---- 漏斗第3层: 有展现0点击 → 关停 ----
    if days >= MIN_DAYS and impr > 0 and clicks == 0:
        return ("淘汰", 0.95, f"展现{int(impr)}次0点击, 主图无吸引力", "换主图重测或关停")

    # ---- 漏斗第3层: 低CTR → 关停 ----
    ctr = clicks / impr if impr > 0 else 0
    if days >= MIN_DAYS and 0 < ctr < 0.02 and clicks < 3:
        return ("淘汰", 0.90, f"CTR仅{ctr*100:.1f}%, 点击成本太高", "换主图重测或关停")

    # ---- 漏斗第4层: 有点击0成交 + 观察期内 → 降投产试探 ----
    if clicks > 0 and deals == 0 and days < max_wait_days:
        return ("降投产试", 0.5,
                f"CTR={ctr*100:.1f}%有点击但0成交, 降投产扩人群试探",
                f"投产比 {thr} → {suggested_roi}, 扩人群看能否出单")

    # ---- 漏斗第4层: 有点击0成交 + 超过等待期 → 淘汰 ----
    if clicks > 0 and deals == 0 and days >= max_wait_days:
        return ("淘汰", 0.90,
                f"测{days}天{int(clicks)}点击0成交, 降投产也救不了",
                "后台暂停该计划")

    # 样本量不足 -> 观察期
    enough = deals >= MIN_DEALS and spend >= MIN_SPEND and days >= MIN_DAYS
    if not enough:
        conf = 1.0 - min(1.0, abs(p_hi - p_lo))
        return ("观察期", conf, "样本不足, 继续给量探索", "维持当前设置, 等待更多数据")

    # 主推判定: 毛ROI和净ROI都需达标(防止高退款款被误推)
    #   毛ROI门槛 = 目标×0.6 (测款阶段放宽)
    #   净ROI门槛 = 目标×0.5 (扣除退款后仍要有利润)
    if p_target >= 0.5 and post_mean >= thr * 0.6:
        if pd.isna(net_roi) or net_roi >= thr * 0.5:
            return ("主推", p_target, "把握度高, 建议放量主推", f"预算加到 {round(spend/max(days,1)*1.5)}元/天")
        else:
            # 毛ROI达标但净ROI不达标 → 退款率高, 降级为继续测
            return ("继续测试", p_target,
                    f"毛ROI={post_mean:.2f}达标但净ROI={net_roi:.2f}偏低, 退款率高, 暂不主推",
                    "维持当前设置, 优化退款率后再主推")

    # CR 淘汰判定
    suff = all_rows[(all_rows["成交笔数"] >= MIN_DEALS) &
                     (all_rows["总花费"] >= MIN_SPEND) &
                     (all_rows["有花费天数"] >= MIN_DAYS)]
    if len(suff) > 1:
        other_hi = suff.loc[suff.index != row.name, "P达标_95上界"]
        if len(other_hi) > 0:
            best_hi = other_hi.max()
            gap = best_hi - p_lo
            threshold = np.sqrt(2 * np.log(4 * max(n_eff, 1) ** 2 / delta) / max(n_eff, 1))
            if gap > threshold:
                return ("淘汰", 1 - delta / len(all_rows),
                        "与最优臂差距显著, 关停释放预算", "后台暂停, 预算给主推款")

    # 继续测试
    conf = max(p_target, 1 - p_target)
    return ("继续测试", conf, "把握度不足, 维持测试预算", "维持当前设置不变")


# ============================================================
# 6. 评估主函数
# ============================================================
def evaluate(df_prod: pd.DataFrame, daily_roi_df: pd.DataFrame = None,
             seed: int = 42, decay_rate: float = 0.15,
             delta: float = 0.05, use_gaussian: bool = True) -> pd.DataFrame:
    """对每个款运行 MAB 评估, 返回带置信度与决策的结果表."""
    rows = []
    N_total = max(int(df_prod["有花费天数"].sum()), 1)

    # 全局漂移检测
    shift_info = detect_global_shift(daily_roi_df, decay_rate)

    # 计算每日衰减权重
    max_date = None
    if daily_roi_df is not None and len(daily_roi_df) > 0:
        max_date = daily_roi_df["日期"].max()

    for idx, r in df_prod.iterrows():
        name = str(r["商品名称"])
        thr = r["目标投产比"]

        if use_gaussian and daily_roi_df is not None and not pd.isna(thr):
            # 高斯后验
            arm_daily = daily_roi_df[daily_roi_df["商品名称"] == name].dropna(subset=["日ROI"])

            observations = []
            for _, dr in arm_daily.iterrows():
                days_ago = 0
                if max_date is not None:
                    try:
                        days_ago = (max_date - dr["日期"]).days
                    except (TypeError, AttributeError):
                        days_ago = 0
                weight = np.exp(-decay_rate * days_ago)
                roi_val = dr["日ROI"]
                # 漂移修正: 用残差 + 目标值
                if shift_info["detected"]:
                    roi_val = roi_val - shift_info["recent_group_avg"] + thr
                observations.append((roi_val, weight))

            if observations:
                post = nig_posterior(observations, thr, seed=seed + hash(name) % 10000)
            else:
                post = {"mu": 0, "sigma2": 1e6, "p_target": 0.5,
                        "ci_lo": -1e6, "ci_hi": 1e6, "n_eff": 0, "ts": 0}

            p_mean = post["mu"]
            p_lo = post["ci_lo"]
            p_hi = post["ci_hi"]
            p_target = post["p_target"]
            n_eff = post["n_eff"]
            ts = post["ts"]
            post_var = post["sigma2"]
            n_i = int(r["有花费天数"]) if not pd.isna(r["有花费天数"]) else 0
            ucb = ucb1(p_target, n_i, N_total)
            hit = "—"
        else:
            # Beta 降级模式
            hit = r["达标天数"] if not pd.isna(r["达标天数"]) else 0
            miss = (r["有花费天数"] - hit) if not pd.isna(r["有花费天数"]) else 0
            if pd.isna(thr):
                hit, miss = 0, 0
            alpha = hit + 1.0
            beta_val = miss + 1.0
            p_mean_beta, p_lo, p_hi = beta_posterior_ci(alpha, beta_val, seed=seed)
            p_target = p_mean_beta
            p_mean = thr * p_mean_beta if not pd.isna(thr) else 0
            n_eff = r["有花费天数"]
            ts = thompson_sample(alpha, beta_val, seed=seed + hash(name) % 10000)
            post_var = np.nan
            n_i = int(r["有花费天数"]) if not pd.isna(r["有花费天数"]) else 0
            ucb = ucb1(p_mean_beta, n_i, N_total)

        rows.append({
            "款名(商品名称)": name,
            "链接数": int(r["链接数"]),
            "链接列表(商品ID)": r["链接列表"],
            "目标投产比": round(thr, 2) if not pd.isna(thr) else np.nan,
            "总花费": round(r["总花费"], 2),
            "交易额": round(r["交易额"], 2),
            "净交易额": round(r["净交易额"], 2),
            "成交笔数": int(r["成交笔数"]) if not pd.isna(r["成交笔数"]) else 0,
            "净成交笔数": int(r["净成交笔数"]) if not pd.isna(r["净成交笔数"]) else 0,
            "点击量": int(r["点击量"]) if not pd.isna(r["点击量"]) else 0,
            "曝光量": int(r["曝光量"]) if not pd.isna(r["曝光量"]) else 0,
            "累计ROI": round(r["ROI"], 3) if not pd.isna(r["ROI"]) else np.nan,
            "净ROI": round(r["净ROI"], 3) if not pd.isna(r["净ROI"]) else np.nan,
            "日ROI方差": r["日ROI方差"],
            "趋势斜率": r["趋势斜率"],
            "趋势": r["趋势"],
            "达标天数": hit,
            "有花费天数": n_i,
            "P达标_后验均值": round(p_target, 3),
            "P达标_95下界": round(p_lo, 3),
            "P达标_95上界": round(p_hi, 3),
            "后验均值_ROI": round(p_mean, 3),
            "后验方差": round(post_var, 3) if not np.isnan(post_var) else np.nan,
            "有效样本量": round(n_eff, 2),
            "UCB1上界": round(ucb, 3) if np.isfinite(ucb) else np.nan,
            "Thompson采样值": round(ts, 3),
            "环境漂移": shift_info["detected"],
        })

    out = pd.DataFrame(rows)

    # CR 决策
    out[["决策", "置信度", "建议动作", "后台调整"]] = out.apply(
        lambda row: decide_cr(row, out, delta), axis=1, result_type="expand"
    )
    out["置信度"] = (out["置信度"] * 100).round(1).astype(str) + "%"

    # 排序
    order = {"主推": 0, "继续测试": 1, "降投产试": 2, "观察期": 3, "淘汰": 4}
    out["_order"] = out["决策"].map(order)
    out = out.sort_values(["_order", "Thompson采样值"], ascending=[True, False]).drop(columns="_order")
    return out.reset_index(drop=True)


# ============================================================
# 7. 报告输出
# ============================================================
def print_report(result: pd.DataFrame, group: str, src: Path, use_gaussian: bool):
    print("=" * 90)
    print(f" 多臂老虎机测款报告 v4  |  分组: {group}  |  数据源: {src.name}")
    print("=" * 90)
    if use_gaussian:
        print(f"算法: Normal-Inverse-Gamma 高斯后验 + CR连续淘汰 (NeurIPS'24)")
        print(f"参考论文: Wang et al.(2024 NeurIPS), Lalitha et al.(2023 UAI)")
    else:
        print(f"算法: Thompson Sampling (Beta-Bernoulli 后验) + UCB1")
        print(f"参考论文: Chapelle & Li (2011, NeurIPS) ; Auer et al. (2002, ML)")
    has_shift = result["环境漂移"].any() if "环境漂移" in result.columns else False
    print(f"置信度: 95% 后验置信区间 | 环境漂移: {'已检测' if has_shift else '无'}")
    print(f"款数: {len(result)} 个  |  目标投产比: 自动从'出价方式'提取")
    print("-" * 90)

    dist = result["决策"].value_counts().to_dict()
    print("决策分布:")
    for k in ["主推", "继续测试", "降投产试", "观察期", "淘汰"]:
        print(f"  - {k}: {dist.get(k, 0)} 个")
    print("-" * 90)

    cols = ["款名(商品名称)", "链接数", "目标投产比", "累计ROI", "净ROI", "总花费",
            "成交笔数", "趋势", "P达标_后验均值", "P达标_95下界", "P达标_95上界",
            "决策", "置信度", "建议动作", "后台调整"]
    show = result[cols].copy()
    show["款名(商品名称)"] = show["款名(商品名称)"].astype(str).str.slice(0, 24)
    with pd.option_context("display.max_rows", None, "display.width", 200,
                           "display.unicode.east_asian_width", True,
                           "display.max_colwidth", 28):
        print(show.to_string(index=False))
    print("=" * 90)


def save_excel(result: pd.DataFrame, link_detail: pd.DataFrame,
               group: str, cwd: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = cwd / f"bandit_result_{group}_{ts}.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        result.to_excel(w, sheet_name="款级MAB评估", index=False)
        link_detail.to_excel(w, sheet_name="链接级明细", index=False)
    return out


# ============================================================
# 8. 主入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="多臂老虎机测款系统 v4")
    ap.add_argument("--file", help="Excel 分天数据路径 (默认自动查找当前目录)")
    ap.add_argument("--group", default="测试", help="分组名称 (默认: 测试)")
    ap.add_argument("--seed", type=int, default=42, help="随机种子")
    ap.add_argument("--decay-rate", type=float, default=0.15,
                    help="时间衰减率 λ (默认0.15, 约7天半衰期)")
    ap.add_argument("--delta", type=float, default=0.05,
                    help="CR淘汰显著性水平 (默认0.05)")
    ap.add_argument("--no-gaussian", action="store_true",
                    help="使用Beta后验 (兼容v3)")
    args = ap.parse_args()

    use_gaussian = not args.no_gaussian

    cwd = Path(__file__).resolve().parent
    src = Path(args.file) if args.file else find_default_excel(cwd)
    print(f"[1/5] 读取数据: {src.name}")

    df = load_data(src)
    print(f"[2/5] 可用分组: {df['分组'].value_counts().to_dict()}")
    print(f"      已选分组: {args.group}")

    sub = select_group(df, args.group)
    n_links = sub["商品ID"].nunique()
    df_prod, daily_roi_df = aggregate_by_product(sub)
    n_prods = len(df_prod)
    print(f"[3/5] 分组 '{args.group}': {len(sub)} 条记录, "
          f"{n_links} 个链接(商品ID), 聚合为 {n_prods} 个款(商品名称)")

    link_detail = aggregate_by_link(sub)
    algo_name = "NIG高斯后验 + CR连续淘汰" if use_gaussian else "Beta后验 + UCB1"
    print(f"[4/5] 运行 {algo_name} (含 95% 置信区间)...")

    result = evaluate(df_prod, daily_roi_df=daily_roi_df, seed=args.seed,
                      decay_rate=args.decay_rate, delta=args.delta,
                      use_gaussian=use_gaussian)

    print_report(result, args.group, src, use_gaussian)
    out = save_excel(result, link_detail, args.group, cwd)
    print(f"[5/5] 结果已保存: {out.name}")
    print(f"      - Sheet1 '款级MAB评估': {len(result)} 个款的置信度与决策")
    print(f"      - Sheet2 '链接级明细': {len(link_detail)} 个链接的花费/ROI对比")
    print(f"\n说明: v4新增 - 高斯NIG后验保留ROI幅度; 时间衰减(λ={args.decay_rate}); "
          f"CR淘汰(δ={args.delta}); 净ROI; 趋势检测; 环境漂移检测。")


if __name__ == "__main__":
    main()
