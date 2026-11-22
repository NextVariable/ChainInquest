"""Deterministic event decoding. Unknown schemas and malformed payloads stay raw."""

import json
import re
from pathlib import Path

from adapters import ADDRESS, HASH
from evidence import QueryError, invalid_constant, quantity, unique_object

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
TOPICS = {TRANSFER: "Transfer", APPROVAL: "Approval"}
BYTES = re.compile(r"0x(?:[0-9a-fA-F]{2})*")


def log_fields(raw):
    if not isinstance(raw, dict):
        raise QueryError("Log must be an object", "invalid_log")
    if raw.get("removed") is not False:
        raise QueryError("Log is removed or missing removed flag", "invalid_log")
    topics = raw.get("topics")
    fields = ("address", "blockHash", "transactionHash", "data")
    patterns = (ADDRESS, HASH, HASH, BYTES)
    if (
        not isinstance(topics, list)
        or len(topics) > 4
        or any(not isinstance(t, str) or not HASH.fullmatch(t) for t in topics)
        or any(
            not isinstance(raw.get(f), str) or not p.fullmatch(raw[f])
            for f, p in zip(fields, patterns)
        )
    ):
        raise QueryError("Malformed log address, hash, topics or data", "invalid_log")
    return {
        "contract": raw["address"].lower(),
        "block_hash": raw["blockHash"].lower(),
        "transaction_hash": raw["transactionHash"].lower(),
        "block": quantity(raw.get("blockNumber")),
        "log_index": quantity(raw.get("logIndex")),
        "transaction_index": quantity(raw.get("transactionIndex")),
        "topics": [t.lower() for t in topics],
        "data": raw["data"].lower(),
    }


def token_event(event):
    topics, data = event["topics"], event["data"]
    if not topics or topics[0] not in TOPICS:
        return None
    if len(topics) != 3 or len(data) != 66 or any(t[2:26] != "0" * 24 for t in topics[1:]):
        raise QueryError("Log signature matches ERC-20 but layout differs", "non_erc20_layout")
    kind = TOPICS[topics[0]]
    names = ("from", "to") if kind == "Transfer" else ("owner", "spender")
    return {
        "kind": kind,
        "classification": "erc20_compatible_event",
        **dict(zip(names, ("0x" + t[-40:] for t in topics[1:]))),
        "amount_raw": str(int(data, 16)),
    }


def decimal_amount(raw, decimals):
    if decimals is None:
        return None
    digits = raw.zfill(decimals + 1)
    return digits[:-decimals] + "." + digits[-decimals:] if decimals else digits


def canonical_type(item):
    value = item["type"]
    if value.startswith("tuple"):
        return "(" + ",".join(canonical_type(i) for i in item["components"]) + ")" + value[5:]
    return value


class AbiDecoder:
    """Caller-supplied verified ABI artifacts, scoped to contract and block interval."""

    def __init__(self, entries, decode, keccak, abi_errors):
        self.entries = entries
        self.decode_words = decode
        self.keccak = keccak
        self.abi_errors = abi_errors

    @classmethod
    def load(cls, path):
        try:
            with Path(path).open("rb") as handle:
                data = handle.read(2**20 + 1)
            if len(data) > 2**20:
                raise ValueError
            entries = json.loads(
                data, object_pairs_hook=unique_object, parse_constant=invalid_constant
            )
            if not isinstance(entries, list):
                raise ValueError
            for item in entries:
                if (
                    not ADDRESS.fullmatch(item["address"])
                    or not isinstance(item["abi"], list)
                    or type(item["from_block"]) is not int
                    or type(item["to_block"]) is not int
                    or not 0 <= item["from_block"] <= item["to_block"]
                    or not isinstance(item["source"], str)
                    or not re.fullmatch(r"https://[^\s@?#]{1,240}", item["source"])
                ):
                    raise ValueError
        except (OSError, ValueError, KeyError, TypeError, RecursionError, QueryError):
            raise QueryError("Invalid ABI artifact manifest", "invalid_abi") from None
        try:
            from eth_abi import decode
            from eth_abi.exceptions import DecodingError, EncodingError, ParseError
            from eth_utils import keccak
        except ImportError:
            raise QueryError(
                "Install optional ABI dependencies to use --abi-file", "abi_dependency"
            ) from None
        return cls(entries, decode, keccak, (DecodingError, EncodingError, ParseError))

    def decode(self, event):
        try:
            return self.decode_event(event)
        except self.abi_errors:
            raise ValueError("ABI codec could not decode log") from None

    def decode_event(self, event):
        if not event["topics"]:
            return None
        matches = []
        for item in self.entries:
            if (
                item["address"].lower() != event["contract"]
                or not item["from_block"] <= event["block"] <= item["to_block"]
            ):
                continue
            for definition in item["abi"]:
                if definition.get("type") != "event" or definition.get("anonymous"):
                    continue
                inputs = definition["inputs"]
                types = [canonical_type(field) for field in inputs]
                signature = definition["name"] + "(" + ",".join(types) + ")"
                if "0x" + self.keccak(text=signature).hex() == event["topics"][0]:
                    matches.append((item, inputs, types, signature))
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("Ambiguous ABI event")
        item, inputs, types, signature = matches[0]
        indexed = [field.get("indexed", False) for field in inputs]
        if (
            any(type(flag) is not bool for flag in indexed)
            or sum(indexed) != len(event["topics"]) - 1
        ):
            raise ValueError("Invalid indexed ABI fields")
        data = bytes.fromhex(event["data"][2:])
        non_indexed = [t for t, flag in zip(types, indexed) if not flag]
        from eth_abi import encode

        decoded = self.decode_words(non_indexed, data)
        if encode(non_indexed, decoded) != data:
            raise ValueError("Noncanonical event data")
        values = iter(decoded)
        topics = iter(event["topics"][1:])
        arguments = []
        for field, t, flag in zip(inputs, types, indexed):
            if flag:
                topic = next(topics)
                if t in {"string", "bytes"} or "[" in t or t.startswith("("):
                    value = {"indexed_hash": topic}
                else:
                    word = bytes.fromhex(topic[2:])
                    result = self.decode_words([t], word)
                    if encode([t], result) != word:
                        raise ValueError("Noncanonical topic")
                    value = result[0]
            else:
                value = next(values)
            arguments.append({"name": field.get("name", ""), "type": t, "value": json_value(value)})
        return {
            "signature": signature,
            "arguments": arguments,
            "source": item["source"],
            "abi_trust": "caller_supplied_verification",
        }


def json_value(value):
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, bool) or isinstance(value, (str, dict)):
        return value
    return str(value)
