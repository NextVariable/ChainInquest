# ChainInquest

[English](README.en.md) · [完整使用说明](docs/usage.md) · [贡献指南](CONTRIBUTING.md)

[![CI](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml/badge.svg)](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml)

面向项目与地址的只读链上调查工具，包含兼容的 Monad 测试网 Agent Skill。给定地址和区块范围，返回顶层交易、回执成功状态、原生币金额、直接发起地址集中度，以及可追溯的交易证据。只读，无需连接钱包；运行只依赖 Python 3.10+ 标准库，curl 可选。

V3 支持提交已核实的项目清单后自动发起调查，核对 Monad 测试网、Base 和 Ethereum 的活动，并输出可复算的证据图谱。运行 `python3 web/server.py`，打开 `http://127.0.0.1:8000` 试用。详见 [V3 使用、实测和部署说明](docs/v3.md)。

## 安装

```sh
git clone https://github.com/NextVariable/ChainInquest.git
cd ChainInquest
skill_destination="${CODEX_HOME:-$HOME/.codex}/skills/onchain-address-inspector"
test ! -e "$skill_destination" && mkdir -p "$(dirname "$skill_destination")" && cp -R skills/onchain-address-inspector "$skill_destination"
```

命令只安装完整 Skill 目录，同名目录存在时停止。更新前请检查自己的修改。必要时重开 Codex 会话，然后发送：

```text
使用 $onchain-address-inspector，核查 Monad 测试网地址 0x000000000000000000000000000000000000dEaD 最近 100 块的活动，说明查询覆盖、发起地址集中度和可核对的交易链接。
```

也可直接运行：

```sh
python3 skills/onchain-address-inspector/scripts/query.py \
  --address 0x000000000000000000000000000000000000dEaD \
  --recent-blocks 100 --progress
```

默认最近 100 块、总预算 120 秒，上限 1000 块。大范围需明确增加预算，例如 `--total-timeout 600`；公共 RPC 无法保证完成。stdout 输出 JSON，stderr 输出进度。完整结果退出码为 0，参数错误或查询不完整为 2。

调查模式支持 ERC-20 形状的 Transfer/Approval、UTC 每日趋势和结构化证据报告。用 `--investigate` 扩展原区块命令，或用 `--days 7`、`--days 30`、`--from-time/--to-time` 查询时间窗口。长窗口依赖索引候选和 RPC 证据，结果会标出覆盖缺口。

V2.1 可用 `--project-file` 汇总已确认合约的同一快照，去重后生成项目调查报告。仓库与显示名为 **ChainInquest**，Skill 调用名为 `$onchain-address-inspector`。 [Project manifest](skills/onchain-address-inspector/references/project.md).

## 结果与边界

先看 `coverage.complete`、`coverage.chain_consistent` 和 `errors`。部分扫描只描述已获取样本；`metrics=null` 表示无法可靠汇总，未知回执不等于交易失败。金额以整数 wei 计算，仅统计成功交易的顶层 value。

直接发起地址集中度包括普通转入、失败和未知状态，不证明真人用户量、刷量或商业采用。调查模式区分顶层原生币金额与代币事件。V3 支持 Base 和 Ethereum 主网。完整历史、内部调用与间接交互仍超出当前覆盖范围。详细参数与口径见[使用说明](docs/usage.md)和[统计边界](skills/onchain-address-inspector/references/sources-and-scope.md)。

## 开发

安装包位于 `skills/onchain-address-inspector/`：`SKILL.md` 提供核心指引，`scripts/` 包含运行代码，`references/` 按需解释参数和口径，`agents/` 提供 Codex 界面信息。仓库层的 `tests/`、`scripts/`、`docs/` 分别用于测试、维护和文档。

```sh
python3 -m unittest discover -s tests -v
```

测试使用人工账本与本机服务，无需公共 RPC。[架构说明](docs/architecture.md)、[验证记录](docs/testing.md)与[贡献指南](CONTRIBUTING.md)提供维护细节。

## 许可

采用 [MIT 许可证](LICENSE)。示例使用通用地址，仅演示参数；请替换为需要核查的目标地址。
