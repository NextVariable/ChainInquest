"""Explicit network registry for the V3 project runner."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    name: str
    symbol: str
    rpc: str
    env: str
    explorer: str

    def endpoint(self):
        return os.environ.get(self.env) or self.rpc


CHAINS = {
    10143: Chain(
        "Monad Testnet",
        "MON",
        "https://rpc-testnet.monadinfra.com",
        "MONAD_TESTNET_RPC",
        "https://testnet.monadscan.com/tx/",
    ),
    8453: Chain(
        "Base Mainnet",
        "ETH",
        "https://mainnet.base.org",
        "CHAININQUEST_BASE_RPC",
        "https://basescan.org/tx/",
    ),
    1: Chain(
        "Ethereum Mainnet",
        "ETH",
        "https://ethereum-rpc.publicnode.com",
        "CHAININQUEST_ETHEREUM_RPC",
        "https://etherscan.io/tx/",
    ),
}
