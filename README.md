# 多臂老虎机测款系统 v5 - 科学测款版

用于电商推广测款的多臂老虎机分析工具。系统读取商品推广/全站推广分天数据 Excel，按商品聚合数据，用 **Beta-Bernoulli CTR 后验多臂**、**UCB 挑战者识别**、**数据量自适应淘汰**、**预算探索价值加权分配** 等顶会论文算法评估每个款的点击潜力。

核心目标：**最小花费找到点击率最高的款**。

## 功能

- 上传 Excel 后在浏览器本地完成测款分析
- 按分组筛选数据，默认支持"测试"分组
- 输出当前好款、挑战者、继续测、暂停重测、淘汰等操作建议
- 展示 CTR 后验均值、95% 置信区间、UCB 值、预算份额等指标
- 支持冷启动保护（前2天不淘汰）、数据量自适应（曝光多更激进）
- 支持 Python 批处理生成 Excel 结果文件
- 支持 Docker + Nginx 部署前端页面

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `index.html` | 测款工作台页面，支持上传 Excel 并在浏览器内分析 |
| `guide.html` | 原理与使用教程页面 |
| `bandit_test.py` | Python 版批处理分析脚本 |
| `Dockerfile` | 前端页面容器构建文件 |
| `nginx.conf` | Nginx 静态站点配置 |
| `商品推广_分天数据_20260626至20260702.xlsx` | 示例/待分析数据文件 |
| `bandit_result_*.xlsx` | Python 脚本生成的分析结果 |

## 网页版使用

直接用浏览器打开 `index.html`，或部署后访问站点首页。

使用步骤：

1. 点击上传区域，选择商品推广或全站推广分天数据 Excel。
2. 选择要分析的分组。
3. 根据需要调整目标 ROI、时间衰减率、淘汰显著性等参数。
4. 点击“开始测款”。
5. 查看页面生成的操作清单、款级评估和明细结果。

说明：网页版使用 SheetJS 在浏览器本地解析 Excel，数据不会上传到服务器。

## Python 批处理使用

### 安装依赖

```bash
pip install pandas numpy scipy openpyxl
```

### 默认运行

脚本会自动读取当前目录下第一个非 `bandit_result` 开头的 Excel 文件，并默认分析“测试”分组。

```bash
python bandit_test.py
```

### 指定文件和分组

```bash
python bandit_test.py --file 商品推广_分天数据_20260626至20260702.xlsx --group 测试
```

### 可选参数

```bash
python bandit_test.py --file 商品推广_分天数据_20260626至20260702.xlsx --group 测试 --seed 42 --decay-rate 0.15 --delta 0.05
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--file` | 自动查找 | Excel 分天数据路径 |
| `--group` | `测试` | 要分析的数据分组 |
| `--seed` | `42` | 随机种子 |
| `--decay-rate` | `0.15` | 时间衰减率，近期数据权重更高 |
| `--delta` | `0.05` | CR 连续淘汰显著性水平 |
| `--no-gaussian` | 关闭 | 使用 Beta 后验兼容旧版本逻辑 |

运行后会生成：

```text
bandit_result_{分组}_{时间戳}.xlsx
```

结果文件包含两个 Sheet：

- `款级MAB评估`：每个款的置信度、ROI、趋势和决策建议
- `链接级明细`：每个商品 ID/链接的花费、ROI 和转化指标

## 输入数据要求

Excel 中需要包含以下常用字段：

- 日期
- 分组
- 商品名称
- 商品ID
- 推广名称
- 出价方式
- 总花费(元)
- 交易额(元)
- 成交笔数
- 点击量
- 曝光量

如果有以下字段，系统会用于更准确的净 ROI 计算：

- 净交易额(元)
- 净成交笔数

`出价方式` 中会自动提取数字作为目标投产比，例如包含 `2.0`、`目标投产比 2.5` 等文本都可以解析。

## Docker 部署

本项目的 Docker 镜像只部署静态前端页面，由 Nginx 提供服务：

- 容器内监听端口：`7021`
- 页面目录：`/usr/share/nginx/html`
- 默认首页：`index.html`
- 教程页面：`/guide.html`

### 本地构建镜像

```bash
docker build -t bandit-workbench .
```

### 本地运行容器

前台运行，停止终端即停止容器：

```bash
docker run --rm -p 7021:7021 bandit-workbench
```

后台运行：

```bash
docker run -d --name bandit-workbench -p 7021:7021 bandit-workbench
```

访问地址：

```text
http://localhost:7021
```

教程页：

```text
http://localhost:7021/guide.html
```

### 停止后台容器

```bash
docker stop bandit-workbench
```

如需重新运行同名容器，先删除旧容器：

```bash
docker rm bandit-workbench
```

### 使用自定义宿主机端口

如果宿主机 `7021` 端口已被占用，可以映射到其他端口，例如：

```bash
docker run --rm -p 8080:7021 bandit-workbench
```

此时访问：

```text
http://localhost:8080
```

### Docker Hub 自动构建

仓库包含 GitHub Actions 工作流 `.github/workflows/docker-build-push.yml`：

- 推送到 `master` 分支时，只构建镜像，不推送。
- 推送到 `main` 分支时，构建并推送到 Docker Hub。
- 镜像标签：`2.0` 和 `latest`。

启用自动推送前，需要在 GitHub 仓库 Secrets 中配置：

| Secret | 说明 |
| --- | --- |
| `DOCKER_HUB_USERNAME` | Docker Hub 用户名 |
| `DOCKER_HUB_TOKEN` | Docker Hub Access Token |

镜像名格式：

```text
<DOCKER_HUB_USERNAME>/smart-product-testing-bandit:latest
```

拉取并运行远程镜像示例：

```bash
docker pull <DOCKER_HUB_USERNAME>/smart-product-testing-bandit:latest
docker run --rm -p 7021:7021 <DOCKER_HUB_USERNAME>/smart-product-testing-bandit:latest
```

## 分析逻辑概览

系统以商品 ID 作为"臂"进行聚合。核心判断基于 **点击率 CTR 后验多臂**，而非 ROI 或成交。

### 算法框架

1. **冷启动保护（前2天）**：不轻易淘汰，只标记观察，避免过早误杀
2. **第3天起按数据量自适应淘汰**：
   - 总曝光 ≥ 10000：更激进（置信度阈值降低）
   - 总曝光 < 3000：更保守（置信度阈值提高）
3. **CTR 后验多臂**：每个款 = 一个臂，点击 = 成功，曝光未点击 = 失败，用 Beta-Bernoulli 计算后验
4. **UCB 挑战者识别**：不只看后验 CTR，还考虑不确定性，识别最有探索价值的候选
5. **提前停止看改进空间**：CTR 差距过大即使置信度不到阈值也可提前淘汰
6. **预算按探索价值加权分配**：当前好款 40%，挑战者 30%，其余按需分配
7. **跑不动单独处理**：测了5天曝光不足的款，建议暂停后调整出价/计划重新开一轮
8. **7天终局胜出**：测款周期结束后，只保留后验 CTR 最高款

### 核心公式

- CTR 后验：`CTR ~ Beta(点击 + 1, 曝光 - 点击 + 1)`
- UCB：`UCB = 后验均值 + 1.96 × sqrt(后验均值 × (1 - 后验均值) / 曝光)`
- 落后好款概率：Monte Carlo 抽样比较，统计 `P(某款 CTR < 当前好款 CTR)`

### 指标分工

| 指标 | 作用 | 是否参与主淘汰 |
|---|---|---|
| 烧钱速度 | 判断能不能跑出流量 | 不直接判断商品好坏 |
| CTR | 判断商品/主图吸引力 | 是，主指标 |
| CVR | 判断点击后的承接能力 | 后期辅助 |
| ROI | 判断是否继续烧钱 | 止损，不做早期主指标 |

### 状态分类

| 状态 | 含义 |
|---|---|
| 当前好款 | 后验 CTR 最高，本轮胜出 |
| 挑战者 | UCB 最高，最有机会超过当前好款 |
| 继续测 | 还没有足够把握输给好款 |
| 暂停重测 | 测了5天曝光不足，建议调整出价/计划后重开一轮 |
| 分轮淘汰 | 置信度输给好款，或差距过大，或终局落选 |

## 参考文献

本系统基于以下顶会论文的 Best Arm Identification 和多臂老虎机理论：

1. **Best Arm Identification**
   - [Optimal Best Arm Identification with Fixed Confidence](https://arxiv.org/abs/1602.04589) - Kaufmann et al., 2016
   - [Fixing the Loose Brake: Exponential-Tailed Stopping Time in Best Arm Identification](https://icml.cc/virtual/2025/poster/46019) - ICML 2025

2. **冷启动与动态先验**
   - [Dynamic Prior Thompson Sampling for Cold-Start Exploration in Recommender Systems](https://arxiv.org/html/2602.00943v1/) - Roblox, 2025
   - [Speed Up the Cold-Start Learning in Two-Sided Bandits with Many Arms](https://pubsonline.informs.org/doi/10.1287/mnsc.2022.03394) - Management Science, 2026

3. **自适应资源分配**
   - [Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization](https://ar5iv.labs.arxiv.org/html/1603.06560) - Li et al., ICLR 2017
   - [Successive Halving and Hyperband](https://intsystems.github.io/materials/blog/hyperband/) - ICML 2016 Tutorial

4. **预算约束与竞价**
   - [Autobidders with Budget and ROI Constraints: Efficiency, Regret, and Pacing Dynamics](https://arxiv.org/html/2301.13306v3/) - COLT 2024

5. **停止规则**
   - [SPRT-based Best Arm Identification in Stochastic Bandits](https://xplorestaging.ieee.org/document/9834534) - IEEE 2022

### 核心思想

> 先给每款一点曝光，然后用 CTR 后验置信度快速淘汰差款，把钱集中花在当前第一名和最可能超过第一名的候选款上。

输出建议主要用于辅助测款决策，最终仍应结合库存、毛利、素材、活动周期和人工经验判断。
