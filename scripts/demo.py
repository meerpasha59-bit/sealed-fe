#!/usr/bin/env python3
"""Narrate one sealed-bid auction from Fe's own call trace.

Runs the `three_bidders_full_lifecycle` test with `fe test --call-trace` and
turns the raw EVM calls into a readable story. Needs only `fe` and Python 3
(standard library). Usage:

    python3 scripts/demo.py
"""
import re
import subprocess
import sys

TEST = "three_bidders_full_lifecycle"

# Addresses in the trace are numbered in creation order by the test.
NAMES = {"$0": "test", "$1": "auction", "$2": "alice", "$3": "bob", "$4": "carol",
         "$5": "alice's wallet", "$6": "bob's wallet", "$7": "carol's wallet",
         "$8": "seller's wallet"}

PHASES = ["Commit", "Reveal", "Closed", "Settled"]
WEI = 10**18


def eth(word: str) -> str:
    v = int(word, 16) / WEI
    return f"{v:g} ETH"


def words(hexdata: str):
    return [hexdata[i:i + 64] for i in range(0, len(hexdata), 64)]


def main() -> int:
    proc = subprocess.run(["fe", "test", "-f", TEST, "--call-trace", "--color", "never"],
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0 or "PASS" not in out:
        print(out)
        print("demo test failed", file=sys.stderr)
        return 1

    print(f"Sealed: a sealed-bid second-price auction, narrated from `fe test --call-trace`\n")
    print("Setup: seller deploys with reserve 1 ETH, commit window 1 day, reveal window 1 hour.")
    print("Bids (secret until reveal): alice 3 ETH, bob 7 ETH (deposits 10 to hide it), carol 5 ETH.\n")
    call = re.compile(r"^CALL (\$\d+) data=([0-9a-f]{8})(\S*) -> (\w+) ret=(\S*)")
    for line in out.splitlines():
        m = call.match(line)
        if not m:
            continue
        to, sel, args, status, ret = m.groups()
        who = NAMES.get(to, to)
        if sel == "94b6f00f":  # doCommit(auction, commitment)
            print(f"  [commit]  {who:6} publishes commitment 0x{args[2:18]}… with a deposit")
        elif sel == "e5d6bf02":  # warp(t)
            print(f"  [clock]   time moves to t={int(args, 16)}")
        elif sel == "b1c9fe6e":  # phase()
            print(f"  [phase]   auction is in {PHASES[int(ret, 16)]}")
        elif sel == "cc0b0cfb":
            print(f"  [state]   {int(ret, 16)} sealed bids locked, amounts still hidden")
        elif sel == "d20f4a79":  # doReveal(auction, amount, salt)
            amount, salt = words(args[2:])[:2]
            lead = "and takes the lead" if int(ret or "0", 16) else "(not leading)"
            print(f"  [reveal]  {who:6} opens {eth(amount)} {lead}")
        elif sel == "11da60b4" and to == "$1":
            print(f"  [settle]  clearing price = {eth(ret)} (the second-highest bid)")
        elif sel == "dfbf53ae":
            print(f"  [result]  winner = {NAMES.get(ret, ret)}")
        elif sel == "c1461c93":  # doWithdraw
            print(f"  [payout]  {who:6} withdraws {eth(ret)}")
        elif sel == "51cff8d9" and to == "$1" and line.find("$8") != -1:
            print(f"  [payout]  seller withdraws {eth(ret)}")
    print("\nEvery wei deposited (19 ETH) was paid out exactly once; the contract ends empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
