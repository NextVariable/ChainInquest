# ChainInquest

[中文](README.md) · [Full usage guide](docs/usage.md) · [Contributing](CONTRIBUTING.md)

[![CI](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml/badge.svg)](https://github.com/NextVariable/ChainInquest/actions/workflows/ci.yml)

An Agent Skill for inspecting wallet and contract activity on Monad Testnet. Given an address and a bounded block range, it returns top-level transactions, receipt outcomes, native-coin amounts, direct-caller concentration, and traceable evidence. Read-only; no wallet connection required. Runtime uses the Python 3.10+ standard library, with optional curl transport.

## Install

```sh
git clone https://github.com/NextVariable/ChainInquest.git
cd ChainInquest
skill_destination="${CODEX_HOME:-$HOME/.codex}/skills/onchain-address-inspector"
test ! -e "$skill_destination" && mkdir -p "$(dirname "$skill_destination")" && cp -R skills/onchain-address-inspector "$skill_destination"
```

Install the complete Skill directory. The command stops if the destination exists; review local modifications before updating. Restart the Codex session if needed, then ask:

```text
Use $onchain-address-inspector to inspect address 0x000000000000000000000000000000000000dEaD on Monad Testnet over the latest 100 blocks. Explain coverage, direct-caller concentration, and traceable transaction links.
```

Or run the CLI directly:

```sh
python3 skills/onchain-address-inspector/scripts/query.py \
  --address 0x000000000000000000000000000000000000dEaD \
  --recent-blocks 100 --progress
```

Defaults: latest 100 blocks, 120-second total budget, maximum 1000 blocks. Larger ranges require an explicit longer budget such as `--total-timeout 600`; public RPC completion is not guaranteed. JSON goes to stdout; progress goes to stderr. Exit code 0 means complete; 2 means invalid arguments or incomplete results.

Investigation mode adds ERC-20-shaped Transfer/Approval events, UTC daily trends and a structured evidence report. Use `--investigate` with block arguments or request `--days 7`, `--days 30` or paired `--from-time/--to-time` timestamps. Long windows rely on index candidates and RPC evidence, with coverage gaps reported explicitly.

## Results and scope

Check `coverage.complete`, `coverage.chain_consistent`, and `errors` first. Partial scans describe retrieved samples only. `metrics=null` means a reliable aggregate is unavailable; unknown receipts are not failed transactions. Native amounts use integer wei and include only successful top-level value.

Direct-caller concentration includes transfers, failed transactions, and unknown outcomes. It does not establish human users, manipulation, or commercial adoption. Investigation mode separates top-level native value from token events. Coverage excludes mainnet, complete history, internal calls and indirect interactions. See [usage](docs/usage.md) and [scope](skills/onchain-address-inspector/references/sources-and-scope.md).

## Development

The installable package lives in `skills/onchain-address-inspector/`: `SKILL.md` contains core instructions, `scripts/` runtime code, `references/` on-demand details, and `agents/` Codex interface metadata. Repository-level `tests/`, `scripts/`, and `docs/` hold regression tests, maintenance tools, and documentation.

```sh
python3 -m unittest discover -s tests -v
```

Tests use synthetic ledgers and local services, without public RPC. See [architecture](docs/architecture.md), [validation](docs/testing.md), and [contributing](CONTRIBUTING.md).

## License

[MIT](LICENSE). The example uses a generic address to demonstrate arguments; replace it with the address you want to inspect.
