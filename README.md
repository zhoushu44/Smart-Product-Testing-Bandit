# 多臂老虎机测款系统 v4

用于电商推广测款的多臂老虎机分析工具。系统读取商品推广/全站推广分天数据 Excel，按商品或链接聚合数据，并用 Thompson Sampling、NIG 高斯后验、CR 连续淘汰、时间衰减等方法评估每个款的投放潜力。

## 功能

- 上传 Excel 后在浏览器本地完成测款分析
- 按分组筛选数据，默认支持“测试”分组
- 输出加预算、保留观察、降预算试探、停止投放等操作建议
- 展示 ROI、净 ROI、CTR、CVR、成交、花费、趋势、置信度等指标
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

构建镜像：

```bash
docker build -t bandit-workbench .
```

运行容器：

```bash
docker run --rm -p 7021:7021 bandit-workbench
```

访问：

```text
http://localhost:8080
```

## 分析逻辑概览

系统以商品名称作为“款”进行聚合，同时保留商品 ID 级别明细。核心判断会综合：

- ROI 是否达到目标投产比
- 净 ROI 表现
- 有花费天数和达标天数
- 最近 ROI 趋势
- 每日 ROI 方差
- NIG 高斯后验置信区间
- Thompson Sampling 排序
- CR 连续淘汰判断

输出建议主要用于辅助投放决策，最终仍应结合库存、毛利、素材、活动周期和人工经验判断。
