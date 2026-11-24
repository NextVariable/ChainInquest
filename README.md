# ChainInquest

**Agent-Native On-chain Investigation Skill & Domain Harness**

[English](README.en.md) · [完整使用说明](docs/usage.md) · [Codex 宿主验收](docs/codex-acceptance.md)

[![CI](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml/badge.svg)](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml)

ChainInquest 是面向 Codex 的只读链上调查 Skill。Codex 理解自然语言目标并决定如何调查。ChainInquest 校验调查范围、查询链上数据、核对证据、计算指标，并在预算内生成可追溯的报告。项目任务支持持久化和恢复。

单地址入口支持 Monad 测试网。项目调查可按已确认合约清单查询 Monad 测试网、Base 主网和 Ethereum 主网。基础运行需要 Python 3.10+，可选用 curl。无需连接钱包或配置独立模型 API。

## Codex 快速开始

在新工作目录安装完整 Skill。若同名目录已经存在，安装命令会保留原目录并提示检查。

```sh
git clone https://github.com/NextVariable/ChainInquest.git
mkdir -p chaininquest-workspace/.agents/skills
skill_destination="chaininquest-workspace/.agents/skills/onchain-address-inspector"
if [ -e "$skill_destination" ] || [ -L "$skill_destination" ]; then
  printf 'Skill already exists at %s\n' "$skill_destination" >&2
else
  cp -R ChainInquest/skills/onchain-address-inspector "$skill_destination"
fi
cd chaininquest-workspace
```

用 Codex 打开 `chaininquest-workspace`，选择工作区安装的 `$onchain-address-inspector`，然后发送自然语言任务：

```text
使用 $onchain-address-inspector 核查 Monad 测试网 USDC 合约
0x534b2f3a21130d7a60830c2df862319e593943a3 最近 12 个区块的活动。
说明顶层交易数量、回执状态、直接发起地址集中度、覆盖范围和可复核证据。
```

调查项目时，提供已确认的链、合约地址、来源和时间范围。请让 Codex 分别报告顶层交易、事件及回执的覆盖。显示名为 **ChainInquest**，Skill 调用名保留 `$onchain-address-inspector`。详细目录规则和直接 CLI 用法见[使用说明](docs/usage.md)与 [Codex Skill 文档](https://developers.openai.com/codex/skills)。

## 验收与边界

三项真实 Codex 宿主验收通过，状态为 **`CODEX_NATIVE_READY`**。这证明宿主能发现 Skill、调用安装包并解释证据。**真实 30 天历史覆盖仍取决于索引、RPC 和预算**。此次 Compound 滚动和固定历史查询都留下覆盖缺口。已核验样本仅描述实际取得的记录，未知活动不能计为零。交易发起地址也不能换算为真人用户。详见[公开验收记录](docs/codex-acceptance.md)。

## 可选组件

- [本地 Web 演示](docs/v4-beta.md#run-locally)可在回环地址试用，独立于 Codex 安装。
- [封闭测试部署模板](deploy/README.md)保留供维护者参考。

当前交付范围为 Codex 原生 Skill 与调查 Harness。MCP Server、普通 ChatGPT 对话接入和公网部署均不属于本次交付。

## 维护

[架构](docs/architecture.md) · [测试记录](docs/testing.md) · [贡献指南](CONTRIBUTING.md) · [MIT 许可证](LICENSE)
