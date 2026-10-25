# 天池复杂表格识别与问答

面向天池「大模型复杂表格识别与内容理解」的个人实践项目。输入图片/PDF 和 Excel 题目清单，调用千问视觉模型读取表格，结合程序校验与计算，输出 `id / answer` 两列 Excel。

## 功能与实现

- 支持 `structure` 表格结构恢复、`extract` 字段提取与 `thinking` 计算推理三类题目。
- 按题意路由；默认单路线求解，由模型返回事实，程序组装表格、枚举与计算答案，执行协议、范围和证据检查。
- PDF 渲染、方向校正、表格局部视图、矢量网格辅助及证据坐标映射；具体能力取决于文本层、网格与版式。
- Decimal 白名单计算、行列锚点检查、同文档事实冲突提示，保留不确定状态与候选来源。
- 有界并发、请求限流和预算、响应缓存、断点恢复、定向重跑、离线合并与导出审计。
- `structured` 路线可显式使用；历史双路线及模型复核需配置 `run.single_pass: false`。当前默认关闭模型复核。

技术栈：Python 3.11、Qwen / DashScope、OpenAI SDK、PyMuPDF、OpenCV、Pillow、NumPy、OpenPyXL、PyYAML。

## 安装

以下命令在本项目目录执行，使用独立虚拟环境：

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

`requirements.lock.txt` 记录原开发环境的依赖版本。同类环境可用 `python -m pip install -c requirements.lock.txt -e .` 约束安装；其他平台及 Python 版本需确认对应安装包可用。macOS / Linux 为现有运行环境，跨进程运行互斥使用 `fcntl.flock`。

## 先运行虚构样例

```sh
python examples/create_demo.py
python app.py check
```

样例生成器会创建一张虚构商品销量表及 3 道题，保存到 `data/`，已有目录则拒绝覆盖。生成和输入检查无需 API Key，不调用模型。

`configs/settings.yaml` 的路径均相对本项目根目录：

| 配置 | 默认路径 |
| --- | --- |
| 题目清单 | `data/questions.xlsx` |
| 图片与 PDF | `data/files/` |
| 提交模板 | `configs/submission-template.xlsx` |
| 缓存 | `cache/` |
| 运行结果 | `outputs/` |

使用自己的资料时，替换上述输入或调整配置。题目表必填列为 `id`、`file_name`、`question_type`、`question`；可选列为 `table_hint`、`answer_format`。ID 应唯一且非空；`answer_format` 支持 `number`、`string`、`json_array`、`json`。文件名相对 `data/files/`，程序检查文件存在及路径边界。

仓库中的提交模板仅有 `id / answer` 表头，不包含比赛答案。`configs/corrections.yaml` 保留原比赛文件别名和题型修正规则，默认关闭；使用新数据集时应先审阅或清空，不要直接启用。

## 模型配置与真实问答

`.env.example` 仅列出变量，程序不自动加载 `.env`。通过终端或 IDE 注入自己的密钥及已开通的模型：

```sh
export DASHSCOPE_API_KEY='填写自己的密钥'
export QWEN_VL_MODEL='填写已开通且支持图片输入的千问模型ID'
export QWEN_TEXT_MODEL="$QWEN_VL_MODEL"
python app.py run --ids demo-1,demo-2,demo-3 --allow-local-processing
```

`run` 会将相关文档图片和问题发送至所配置的阿里云服务，产生真实调用。默认端点为 DashScope 兼容接口，模型名称及服务能力需按自己的账号配置。上面的本地处理开关适用于自己的学习样例；正式参赛时应先确认竞赛对本地解析、图像处理及计算的许可范围。

输入检查通过后，省略 `--ids` 可处理整个题目清单。使用 `--json` 返回完整报告，常规模式显示题目状态、请求次数、Token 统计及产物路径。

## 恢复、迭代与导出

```sh
# 同一代码、配置和输入下继续运行
python app.py resume --run 运行编号 --allow-local-processing

# 以旧运行作基线，仅重跑选定题目，生成新运行
python app.py rerun --base 运行编号 --ids demo-1 --allow-local-processing

# 离线重新导出或比较两个运行，不调用模型
python app.py export --run 运行编号
python app.py compare --left 旧运行编号 --right 新运行编号

# 合并同一数据集的运行，冲突需审阅
python app.py merge --runs 运行编号A,运行编号B
```

默认 `run.fill_missing_answers: true` 尽力生成非空提交，可能沿用历史候选或使用类型占位值。应同时检查 `report.json` 的 `placeholder_count`、`uncertain_submitted_count`、`baseline_fallback_ids` 和导出审计；非空答案或 `success` 状态不代表比赛答对。

`outputs/<run_id>/` 保存结果、快照、请求追踪、报告和 `submission.xlsx`；`submission_answers.json` 与 `submission_fillings.json` 记录提交文本及补齐来源。更换代码、提示或输入后应使用新运行或 `rerun`，保留历史记录便于比较。

## 目录与发布范围

```text
app.py                 命令入口
src/table_qa/
  application/         批处理调度、流水线与运行合并
  processing/          页面处理、局部视图与表格识别
  solving/             模型求解、事实组装与受控计算
  validation/          协议、坐标、范围及答案校验
  infrastructure/      模型调用、缓存、Excel 与追踪
configs/               参数、提示词、路由、修正规则与空模板
examples/create_demo.py 虚构输入生成器
tests/                 自建样例与模拟响应的离线回归
```

本目录是原学习工程的发布副本。比赛原始图片/PDF、题目集、已有提交答案、模型响应、运行记录、缓存、虚拟环境和密钥均不发布。原工程中逐题调试的 README 与内部设计记录也不复制到仓库，使用说明以本文件为准。

## 本地验证

测试采用合成资料与模拟模型响应，不请求付费模型。验证中间文件保存在已忽略的 `.codex-tmp/`：

```sh
mkdir -p .codex-tmp
TMPDIR="$PWD/.codex-tmp" python -m unittest discover -s tests -q
```

该验证覆盖输入、缓存、坐标、表格结构、计算、断点恢复、运行合并与 Excel 导出。测试通过不等于真实识别准确率；新环境安装、真实服务连通性及官方评分应分别验证。
