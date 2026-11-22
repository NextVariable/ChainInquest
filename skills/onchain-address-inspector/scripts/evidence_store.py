"""Per-investigation bounded receipt cache and JSON evidence bundle."""

import json

from evidence import QueryError


class EvidenceStore:
    def __init__(self, cache_bytes=16 * 1024 * 1024):
        self.receipts = {}
        self.remaining = cache_bytes

    def receipt(self, rpc, tx_hash):
        if tx_hash not in self.receipts:
            try:
                result = rpc.call("eth_getTransactionReceipt", [tx_hash])
            except QueryError as error:
                self.receipts[tx_hash] = error
                raise
            size = len(json.dumps(result, ensure_ascii=True))
            if size <= self.remaining:
                self.receipts[tx_hash] = result
                self.remaining -= size
            return result
        value = self.receipts[tx_hash]
        if isinstance(value, QueryError):
            raise value
        return value

    @staticmethod
    def export(txs, events, rejected, stable):
        return {
            "transactions": txs,
            "events": events,
            "unverified_logs": rejected,
            "snapshot_checked": stable,
        }
