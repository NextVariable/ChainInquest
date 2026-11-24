# ChainInquest

**Agent-Native On-chain Investigation Skill & Domain Harness**

[中文](README.md) · [Usage guide](docs/usage.md) · [Codex host acceptance](docs/codex-acceptance.md)

[![CI](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml/badge.svg)](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml)

ChainInquest is a read-only investigation Skill for Codex. Codex interprets the natural-language objective and decides how to investigate. ChainInquest validates scope, queries chain data, checks evidence, computes metrics, and generates traceable reports within explicit budgets. Project investigations also support persisted state and resumption.

Single-address checks target Monad Testnet. Project investigations can query Monad Testnet, Base Mainnet, and Ethereum Mainnet using a confirmed contract manifest. The base runtime requires Python 3.10+, with optional curl transport. No wallet connection or separate model API is required.

## Codex quickstart

Copy the complete Skill into a new investigation workspace. The command preserves an existing installation and prints its location for review.

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

Open `chaininquest-workspace` in Codex, select the workspace copy of `$onchain-address-inspector`, and ask in natural language:

```text
Use $onchain-address-inspector to inspect the Monad Testnet USDC contract
0x534b2f3a21130d7a60830c2df862319e593943a3 over the latest 12 blocks.
Explain top-level transaction counts, receipt outcomes, direct-caller concentration,
coverage, and verifiable evidence.
```

For a project investigation, provide confirmed networks, contract addresses, sources, and a time range. Ask Codex to report transaction, event, and receipt coverage separately. **ChainInquest** is the display name and `$onchain-address-inspector` remains the invocation name. See the [usage guide](docs/usage.md) and [Codex Skill documentation](https://developers.openai.com/codex/skills).

## Acceptance and scope

Three live Codex host cases passed, establishing **`CODEX_NATIVE_READY`**. The host discovered the Skill, ran the installed package, and interpreted the evidence. **Complete 30-day historical coverage depends on the index, RPC, and query budget.** Both Compound windows in this acceptance run had gaps. Verified samples describe the records received. Unknown activity cannot be counted as zero, and address counts cannot establish human user counts. See the [public acceptance record](docs/codex-acceptance.md).

## Optional components

- The [local Web demo](docs/v4-beta.md#run-locally) runs on loopback and is independent of the Codex installation.
- [Closed-beta deployment templates](deploy/README.md) are retained as a maintainer reference.

The delivery covers the Codex-native Skill and investigation Harness. MCP servers, integration with standard ChatGPT conversations, and public deployment are outside the delivery scope.

## Maintenance

[Architecture](docs/architecture.md) · [Testing](docs/testing.md) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
