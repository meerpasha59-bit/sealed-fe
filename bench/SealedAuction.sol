// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

/// Reference implementation of the same auction in Solidity, written for the
/// gas comparison in bench/. Same ABI, same rules, same custom errors and
/// events as src/sealed.fe, in idiomatic Solidity (checked arithmetic, custom
/// errors, pull payments). Not part of the Fe project itself.
contract SealedAuction {
    error WrongPhase(uint8 expected, uint8 actual);
    error AlreadyCommitted(address bidder);
    error EmptyCommit();
    error SellerCannotBid();
    error NoCommitment(address bidder);
    error CommitmentMismatch();
    error AlreadyRevealed();
    error InvalidBid(uint256 amount, uint256 deposit);
    error NothingToWithdraw();

    event Committed(address indexed bidder, uint256 commitment, uint256 deposit);
    event Revealed(address indexed bidder, uint256 amount, bool leading);
    event Settled(address indexed winner, uint256 price, uint256 bidders, uint256 reveals);
    event Withdrawn(address indexed account, address to, uint256 amount);

    uint8 constant COMMIT = 0;
    uint8 constant REVEAL = 1;
    uint8 constant CLOSED = 2;
    uint8 constant SETTLED = 3;

    address public seller;
    uint256 public commitEnd;
    uint256 public revealEnd;
    uint256 public reserve;
    bool public settled;
    address internal leader;
    uint256 public highestBid;
    uint256 internal secondBid;
    uint256 public reveals;
    uint256 public price;
    uint256 public bidders;
    uint256 internal totalDeposits;
    uint256 internal revealedDeposits;
    mapping(address => uint256) public commitmentOf;
    mapping(address => uint256) public depositOf;
    mapping(address => bool) internal revealed;
    mapping(address => bool) internal withdrawn;

    constructor(uint256 commitPeriod, uint256 revealPeriod, uint256 reserve_) {
        seller = msg.sender;
        commitEnd = block.timestamp + commitPeriod;
        revealEnd = commitEnd + revealPeriod;
        reserve = reserve_;
    }

    function phase() public view returns (uint8) {
        if (settled) return SETTLED;
        if (block.timestamp < commitEnd) return COMMIT;
        if (block.timestamp < revealEnd) return REVEAL;
        return CLOSED;
    }

    function _require(uint8 expected) internal view {
        uint8 actual = phase();
        if (actual != expected) revert WrongPhase(expected, actual);
    }

    function commit(uint256 commitment) external payable {
        _require(COMMIT);
        if (msg.sender == seller) revert SellerCannotBid();
        if (msg.value == 0 || commitment == 0) revert EmptyCommit();
        if (commitmentOf[msg.sender] != 0) revert AlreadyCommitted(msg.sender);
        commitmentOf[msg.sender] = commitment;
        depositOf[msg.sender] = msg.value;
        bidders += 1;
        totalDeposits += msg.value;
        emit Committed(msg.sender, commitment, msg.value);
    }

    function reveal(uint256 amount, uint256 salt) external returns (bool leading) {
        _require(REVEAL);
        uint256 stored = commitmentOf[msg.sender];
        if (stored == 0) revert NoCommitment(msg.sender);
        if (revealed[msg.sender]) revert AlreadyRevealed();
        if (stored != uint256(keccak256(abi.encode(address(this), msg.sender, amount, salt)))) {
            revert CommitmentMismatch();
        }
        uint256 deposit = depositOf[msg.sender];
        if (amount == 0 || amount > deposit) revert InvalidBid(amount, deposit);
        revealed[msg.sender] = true;
        revealedDeposits += deposit;
        if (reveals == 0 || amount > highestBid) {
            secondBid = highestBid;
            highestBid = amount;
            leader = msg.sender;
        } else if (amount > secondBid) {
            secondBid = amount;
        }
        reveals += 1;
        leading = leader == msg.sender;
        emit Revealed(msg.sender, amount, leading);
    }

    function _sold() internal view returns (bool) {
        return reveals > 0 && highestBid >= reserve;
    }

    function settle() external returns (uint256) {
        _require(CLOSED);
        uint256 p = 0;
        if (_sold()) p = secondBid > reserve ? secondBid : reserve;
        price = p;
        settled = true;
        emit Settled(_sold() ? leader : address(0), p, bidders, reveals);
        return p;
    }

    function winner() external view returns (address) {
        return _sold() ? leader : address(0);
    }

    function claimable(address account) public view returns (uint256) {
        if (!settled || withdrawn[account]) return 0;
        if (account == seller) return price + (totalDeposits - revealedDeposits);
        if (!revealed[account]) return 0;
        if (_sold() && leader == account) return depositOf[account] - price;
        return depositOf[account];
    }

    function withdraw(address to) external returns (uint256 amount) {
        _require(SETTLED);
        amount = claimable(msg.sender);
        if (amount == 0) revert NothingToWithdraw();
        withdrawn[msg.sender] = true;
        (bool ok,) = to.call{value: amount, gas: 0}("");
        require(ok, "Transfer failed");
        emit Withdrawn(msg.sender, to, amount);
    }
}
