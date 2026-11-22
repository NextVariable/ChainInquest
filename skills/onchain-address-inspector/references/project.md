# 项目级调查 / Project investigation

先用现有的 `--address ... --investigate` 分别保存报告。使用同一组固定区块区间，或同一对带时区的固定绝对时间。各输入报告必须具有同一网络、解析后的区块和时间范围、结束区块哈希，以及成功的快照复查。滚动窗口通常不适合跨地址拼接。使用 V2.1 生成的地址报告，旧报告缺少快照锚点或证据引用时需重查。

Create the manifest and saved reports under the same directory. Paths are confined to that directory. Run `python3 scripts/query.py --project-file project.json` from the Skill directory, using the actual path to your manifest. The project mode reads local evidence only.

```json
{
  "schema_version": "1",
  "project": "Example project",
  "deployments": [{
    "id": "testnet-v1",
    "chain_id": 10143,
    "contracts": [{
      "address": "0x000000000000000000000000000000000000dEaD",
      "deployment_block": 123,
      "deployed_at": "2026-01-01T00:00:00Z",
      "attribution": {
        "status": "confirmed",
        "sources": ["https://example.org/official-deployment"]
      },
      "report_file": "contract-a.json"
    }]
  }]
}
```

示例地址、部署信息和网址仅展示格式。实际使用时请先查证项目公开部署说明、创建交易等材料。状态支持 `confirmed`、`unconfirmed`、`disputed`。只有已确认且附有来源、部署区块和时间的地址可纳入统计。确认由清单维护者提供，程序不会访问来源 URL 或自动查证归属。未确认和存疑合约列在报告里，统计时排除。最多 32 个地址。报告为本地证据产品，请使用可信来源生成的文件。

Project output is `project-2.1`. Transactions are deduplicated by transaction hash within the shared chain snapshot. Logs are deduplicated by block and log index. Conflicting evidence for the same identity is excluded and recorded as a gap. Each record identifies the input contracts whose reports supplied it. Evidence before the claimed deployment block or time is ignored for that contract. Successful top-level native value is partitioned into project inflow, outflow, and internal movement at the time of the transaction. Token-event counts and native value use separate units.

Project completeness applies to the confirmed manifest perimeter and submitted reports. Missing or incompatible confirmed reports yield exit code 2. Unconfirmed entries remain visible as exclusions. Partial metrics describe the evidence checked, including any zero counts. They cannot establish absence of activity where coverage is missing. A shared ending hash is a consistency check. This does not authenticate caller-supplied attestations or establish legal ownership, wallet identity, or commercial adoption.
