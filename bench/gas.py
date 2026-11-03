#!/usr/bin/env python3
"""Gas comparison: Fe `SealedAuction` vs. the Solidity reference.

Runs the same three-bidder auction (the one in scripts/demo.py) against
  * Fe:       out/SealedAuction.bin from `fe build` (Sonatina, -O1 and -O2)
  * Solidity: bench/SealedAuction.sol with solc 0.8.28, legacy and via-IR,
              both with the optimizer (200 runs)
on py-evm (Cancun rules) with real signed transactions and real block
timestamps, and prints gas used per transaction (21,000 intrinsic gas and
calldata included, exactly what a user pays).

Requirements: fe, solc 0.8.28 on PATH (or $SOLC), Python 3 with py-evm:
    pip install py-evm==0.12.1b1
Usage:
    python3 bench/gas.py            # table to stdout
    python3 bench/gas.py --md       # markdown table (used in the README)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from eth import constants
from eth.chains.base import MiningChain
from eth.db.atomic import AtomicDB
from eth.vm.forks import CancunVM
from eth_keys import keys
from eth_utils import keccak, to_canonical_address, to_wei

ROOT = Path(__file__).resolve().parent.parent
SOLC = os.environ.get("SOLC", "solc")
START = 1_800_000_000
DAY = 86_400
HOUR = 3_600


def fe_bytecode(opt: str) -> bytes:
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(["fe", "build", "-O", opt, "--out-dir", out], cwd=ROOT, check=True,
                       capture_output=True)
        return bytes.fromhex(Path(out, "SealedAuction.bin").read_text().strip())


def sol_bytecode(via_ir: bool) -> bytes:
    args = [SOLC, "--optimize", "--optimize-runs", "200", "--bin", "--evm-version", "cancun"]
    if via_ir:
        args.append("--via-ir")
    args.append(str(ROOT / "bench" / "SealedAuction.sol"))
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    lines = out.splitlines()
    idx = lines.index("Binary:")
    return bytes.fromhex(lines[idx + 1].strip())


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def word(v: int) -> bytes:
    return v.to_bytes(32, "big")


def addr_word(a: bytes) -> bytes:
    return b"\x00" * 12 + a


class Chain:
    def __init__(self):
        self.keys = {name: keys.PrivateKey(keccak(text=name)) for name in
                     ["seller", "alice", "bob", "carol"]}
        self.addr = {n: k.public_key.to_canonical_address() for n, k in self.keys.items()}
        state = {a: {"balance": to_wei(1000, "ether"), "nonce": 0, "code": b"", "storage": {}}
                 for a in self.addr.values()}
        params = {
            "difficulty": 0, "gas_limit": 30_000_000, "timestamp": START - 100,
            "coinbase": constants.ZERO_ADDRESS, "extra_data": b"", "nonce": b"\x00" * 8,
            "mix_hash": constants.ZERO_HASH32, "base_fee_per_gas": 1,
        }
        klass = MiningChain.configure(__name__="Bench", vm_configuration=((0, CancunVM),),
                                      chain_id=1337)
        self.chain = klass.from_genesis(AtomicDB(), params, state)
        self.nonce = {n: 0 for n in self.keys}
        self.now = START
        self.last = START - 100

    def at(self, t: int):
        self.now = t

    def tick(self) -> int:
        # Every block needs a strictly later timestamp than its parent.
        self.last = max(self.now, self.last + 1)
        return self.last

    def send(self, who: str, to: bytes, data: bytes, value: int = 0):
        vm = self.chain.get_vm()
        tx = vm.create_unsigned_transaction(nonce=self.nonce[who], gas_price=10, gas=5_000_000,
                                            to=to, value=value, data=data)
        ts = self.tick()
        self.chain.header = self.chain.header.copy(timestamp=ts)
        signed = tx.as_signed_transaction(self.keys[who], chain_id=1337)
        _, receipt, computation = self.chain.apply_transaction(signed)
        self.chain.mine_block(timestamp=ts)
        self.nonce[who] += 1
        if computation.is_error:
            raise RuntimeError(f"{who} tx failed: {computation.error!r} {computation.output.hex()}")
        return receipt.gas_used, computation

    def deploy(self, code: bytes, args: bytes):
        vm = self.chain.get_vm()
        tx = vm.create_unsigned_transaction(nonce=self.nonce["seller"], gas_price=10,
                                            gas=10_000_000, to=constants.CREATE_CONTRACT_ADDRESS,
                                            value=0, data=code + args)
        ts = self.tick()
        self.chain.header = self.chain.header.copy(timestamp=ts)
        signed = tx.as_signed_transaction(self.keys["seller"], chain_id=1337)
        _, receipt, computation = self.chain.apply_transaction(signed)
        self.chain.mine_block(timestamp=ts)
        self.nonce["seller"] += 1
        if computation.is_error:
            raise RuntimeError(f"deploy failed: {computation.error!r}")
        return receipt.gas_used, computation.msg.storage_address

    def balance(self, a: bytes) -> int:
        return self.chain.get_vm().state.get_balance(a)

    def call(self, to: bytes, data: bytes) -> bytes:
        vm = self.chain.get_vm()
        tx = vm.create_unsigned_transaction(nonce=0, gas_price=10, gas=1_000_000, to=to,
                                            value=0, data=data)
        signed = tx.as_signed_transaction(self.keys["seller"], chain_id=1337)
        return self.chain.get_transaction_result(signed, self.chain.get_canonical_head())


def run(code: bytes):
    c = Chain()
    eth = 10**18
    rows = []
    c.at(START)
    g, auction = c.deploy(code, word(DAY) + word(HOUR) + word(1 * eth))
    rows.append(("deploy", g))
    bids = {"alice": (3, 3, 0xA), "bob": (7, 10, 0xB), "carol": (5, 6, 0xC)}

    def commitment(name):
        amount, _, salt = bids[name]
        return keccak(addr_word(auction) + addr_word(c.addr[name]) + word(amount * eth) + word(salt))

    labels = {"alice": "commit (first bidder)", "bob": "commit", "carol": "commit (3rd)"}
    for name in ["alice", "bob", "carol"]:
        g, _ = c.send(name, auction, selector("commit(uint256)") + commitment(name),
                      value=bids[name][1] * eth)
        rows.append((labels[name], g))

    c.at(START + DAY)
    labels = {"alice": "reveal (first, leads)", "bob": "reveal (takes the lead)",
              "carol": "reveal (not leading)"}
    for name in ["alice", "bob", "carol"]:
        amount, _, salt = bids[name]
        g, _ = c.send(name, auction, selector("reveal(uint256,uint256)") + word(amount * eth)
                      + word(salt))
        rows.append((labels[name], g))

    c.at(START + DAY + HOUR)
    g, comp = c.send("alice", auction, selector("settle()"))
    assert int.from_bytes(comp.output, "big") == 5 * eth, comp.output.hex()
    rows.append(("settle", g))

    payees = {n: to_canonical_address("0x" + f"{0xbeef0 + i:040x}") for i, n in
              enumerate(["alice", "bob", "carol", "seller"])}
    labels = {"alice": "withdraw (loser)", "bob": "withdraw (winner)",
              "carol": "withdraw (loser, over-deposit)", "seller": "withdraw (seller)"}
    expected = {"alice": 3, "bob": 5, "carol": 6, "seller": 5}
    for name in ["alice", "bob", "carol", "seller"]:
        g, _ = c.send(name, auction, selector("withdraw(address)") + addr_word(payees[name]))
        assert c.balance(payees[name]) == expected[name] * eth, name
        rows.append((labels[name], g))
    assert c.balance(auction) == 0
    return rows, len(code)


def main():
    variants = [
        ("Fe -O1", fe_bytecode("1")),
        ("Fe -O2", fe_bytecode("2")),
        ("solc", sol_bytecode(False)),
        ("solc via-IR", sol_bytecode(True)),
    ]
    results = {}
    sizes = {}
    for name, code in variants:
        rows, size = run(code)
        results[name] = rows
        sizes[name] = size
    names = [n for n, _ in variants]
    labels = [label for label, _ in results[names[0]]]
    md = "--md" in sys.argv
    best_sol = lambda i: min(results["solc"][i][1], results["solc via-IR"][i][1])
    if md:
        print("| Transaction | " + " | ".join(names) + " | Fe -O2 vs best solc |")
        print("| --- | " + " | ".join("---:" for _ in names) + " | ---: |")
    else:
        print(f"{'transaction':32}" + "".join(f"{n:>13}" for n in names) + f"{'Fe-O2 vs solc':>15}")
    total = {n: 0 for n in names}
    for i, label in enumerate(labels):
        vals = [results[n][i][1] for n in names]
        for n, v in zip(names, vals):
            if label != "deploy":
                total[n] += v
        delta = (results["Fe -O2"][i][1] - best_sol(i)) / best_sol(i) * 100
        if md:
            print(f"| {label} | " + " | ".join(f"{v:,}" for v in vals) + f" | {delta:+.1f}% |")
        else:
            print(f"{label:32}" + "".join(f"{v:>13,}" for v in vals) + f"{delta:>+14.1f}%")
    best_total = min(total["solc"], total["solc via-IR"])
    d = (total["Fe -O2"] - best_total) / best_total * 100
    if md:
        print("| **all 11 auction txs** | " + " | ".join(f"**{total[n]:,}**" for n in names)
              + f" | **{d:+.1f}%** |")
        print("| init code size (bytes) | " + " | ".join(f"{sizes[n]:,}" for n in names) + " | |")
    else:
        print(f"{'all 11 auction txs':32}" + "".join(f"{total[n]:>13,}" for n in names) + f"{d:>+14.1f}%")
        print(f"{'init code size (bytes)':32}" + "".join(f"{sizes[n]:>13,}" for n in names))


if __name__ == "__main__":
    main()
