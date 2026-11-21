"""Direct mode tests for contracts/prediction_market.py.

Every web page and LLM answer is mocked, so the tests exercise the
deterministic contract logic, the lifecycle windows and the leader/validator
comparison without touching the network.

Payouts are pulled: ``claim`` credits a balance inside the contract and
``withdraw`` sends the GEN out. Direct mode has no chain layer, so that last
hop is skipped.
"""

import pytest

from tests.direct.conftest import to_hex

CONTRACT = "contracts/prediction_market.py"
GEN = 10 ** 18

MARKET_URL = "https://example.com/result"
MARKET_PAGE = (
    "<html><body><h1>Final result</h1>"
    "<p>Indonesia reached the final round.</p></body></html>"
)

RESOLVE_AFTER = 2_000_000_000              # 2033-05-18, after the test clock
REFUND_AFTER = 2_100_000_000               # later window for the refund escape
AFTER_RESOLVE = "2033-05-19T00:00:00Z"
AFTER_REFUND = "2036-08-01T00:00:00Z"

YES_ANSWER = (
    '{"outcome": "yes", "confidence": 92, "source_url": "https://example.com/result", '
    '"reason": "The page says Indonesia reached the final round."}'
)
NO_ANSWER = (
    '{"outcome": "no", "confidence": 88, "source_url": "https://example.com/result", '
    '"reason": "The page says Indonesia did not reach the final round."}'
)


def create_market(contract, resolve_after=RESOLVE_AFTER, refund_after=REFUND_AFTER):
    return contract.create_market(
        "Did Indonesia reach the final round?",
        "Answer yes if Indonesia played in the final round, otherwise no.",
        MARKET_URL,
        resolve_after,
        refund_after,
    )


def mock_resolution(direct_vm, llm_answer, page=MARKET_PAGE):
    direct_vm.mock_web(r"example\.com/result", {"status": 200, "body": page})
    direct_vm.mock_llm(r"impartial resolver", llm_answer)


def stake(direct_vm, contract, side, amount, market_id=0):
    direct_vm.value = amount
    if side == "yes":
        contract.stake_yes(market_id)
    else:
        contract.stake_no(market_id)
    direct_vm.value = 0


# ---------------------------------------------------------------------------
# Market creation
# ---------------------------------------------------------------------------

def test_create_market(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    market_id = create_market(contract)

    assert market_id == 0
    assert contract.get_market_count() == 1
    assert contract.get_question(0) == "Did Indonesia reach the final round?"
    assert contract.get_sources(0) == MARKET_URL
    assert contract.get_outcome(0) == ""
    assert contract.get_total_pool(0) == 0
    assert contract.get_resolve_after(0) == RESOLVE_AFTER
    assert contract.get_refund_after(0) == REFUND_AFTER
    assert to_hex(contract.get_creator(0)) == to_hex(direct_alice)


def test_create_market_requires_http_source(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("source URL must start with http"):
        contract.create_market(
            "Pertanyaan?", "Kriteria resolusi.", "ftp://example.com/x",
            RESOLVE_AFTER, REFUND_AFTER,
        )


def test_create_market_rejects_more_than_three_sources(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    sources = "\n".join("https://example.com/%d" % i for i in range(4))

    with direct_vm.expect_revert("at most 3 source URLs"):
        contract.create_market(
            "Pertanyaan?", "Kriteria resolusi.", sources, RESOLVE_AFTER, REFUND_AFTER
        )


def test_create_market_requires_a_future_resolution_time(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("resolution time must be in the future"):
        create_market(contract, resolve_after=1_000_000_000)


def test_create_market_requires_refund_after_resolution(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("refund time must be after the resolution time"):
        create_market(contract, refund_after=RESOLVE_AFTER - 1)


def test_unknown_market_views_are_empty(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    assert contract.get_question(7) == ""
    assert contract.get_outcome(7) == ""
    assert contract.get_total_pool(7) == 0
    assert contract.get_my_stake(7) == 0


# ---------------------------------------------------------------------------
# Staking
# ---------------------------------------------------------------------------

def test_stake_pools_and_side(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", 3 * GEN)
    assert contract.get_my_side(0) == "yes"
    assert contract.get_my_stake(0) == 3 * GEN

    direct_vm.sender = direct_bob
    stake(direct_vm, contract, "no", GEN)
    assert contract.get_my_side(0) == "no"
    assert contract.get_my_stake(0) == GEN

    assert contract.get_yes_pool(0) == 3 * GEN
    assert contract.get_no_pool(0) == GEN
    assert contract.get_total_pool(0) == 4 * GEN


def test_second_stake_on_the_same_side_adds_up(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", GEN)
    stake(direct_vm, contract, "yes", 2 * GEN)

    assert contract.get_my_stake(0) == 3 * GEN
    assert contract.get_yes_pool(0) == 3 * GEN


def test_cannot_stake_on_both_sides(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", GEN)

    direct_vm.value = GEN
    with direct_vm.expect_revert("you already staked on the other side"):
        contract.stake_no(0)
    direct_vm.value = 0


def test_stake_below_minimum_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    direct_vm.value = GEN // 200
    with direct_vm.expect_revert("stake is below the minimum"):
        contract.stake_yes(0)
    direct_vm.value = 0


def test_betting_closes_at_the_resolution_time(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    direct_vm.warp(AFTER_RESOLVE)

    direct_vm.value = GEN
    with direct_vm.expect_revert("betting is closed"):
        contract.stake_no(0)
    direct_vm.value = 0


def test_resolve_before_the_resolution_time_reverts(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)

    with direct_vm.expect_revert("cannot be resolved before its resolution time"):
        contract.resolve(0)


def test_resolve_without_stakes_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    direct_vm.warp(AFTER_RESOLVE)

    with direct_vm.expect_revert("market has no stakes yet"):
        contract.resolve(0)


# ---------------------------------------------------------------------------
# Resolution and payout
# ---------------------------------------------------------------------------

def test_resolve_yes_and_payout_preview(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", 3 * GEN)

    direct_vm.sender = direct_bob
    stake(direct_vm, contract, "no", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    direct_vm.warp(AFTER_RESOLVE)

    assert contract.resolve(0) == "yes"
    assert contract.get_outcome(0) == "yes"
    assert contract.get_resolution_reason(0) != ""
    # The stored source must be one of the URLs submitted at creation.
    assert contract.get_resolution_source(0) == MARKET_URL

    # Alice risked 3 of the 4 GEN pool, so she takes the whole pool.
    assert contract.preview_payout(0, to_hex(direct_alice)) == 4 * GEN
    assert contract.preview_payout(0, to_hex(direct_bob)) == 0
    assert not contract.has_claimed(0)
    assert contract.get_my_owed() == 0


def test_claim_credits_the_winner(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", 3 * GEN)

    direct_vm.sender = direct_bob
    stake(direct_vm, contract, "no", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    direct_vm.warp(AFTER_RESOLVE)
    contract.resolve(0)

    direct_vm.sender = direct_alice
    assert contract.claim(0) == 4 * GEN
    assert contract.get_my_owed() == 4 * GEN
    assert contract.has_claimed(0)

    with direct_vm.expect_revert("you already claimed"):
        contract.claim(0)

    # The losing side claims nothing but is marked as settled.
    direct_vm.sender = direct_bob
    assert contract.claim(0) == 0
    assert contract.get_my_owed() == 0
    assert contract.has_claimed(0)


def test_claim_before_resolution_reverts(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    with direct_vm.expect_revert("market is not resolved yet"):
        contract.claim(0)


def test_resolve_twice_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    direct_vm.warp(AFTER_RESOLVE)
    contract.resolve(0)

    with direct_vm.expect_revert("market is already resolved"):
        contract.resolve(0)


# ---------------------------------------------------------------------------
# Timeout escape: the refund window
# ---------------------------------------------------------------------------

def test_void_before_the_refund_window_reverts(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    direct_vm.warp(AFTER_RESOLVE)

    with direct_vm.expect_revert("the refund window has not opened yet"):
        contract.void_market(0)


def test_anyone_can_void_after_the_refund_window(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", 2 * GEN)

    direct_vm.sender = direct_bob
    stake(direct_vm, contract, "no", GEN)

    # The creator cannot pull this lever early, and the escape is not reserved
    # to the creator either: a stranger can settle the deadlock.
    direct_vm.warp(AFTER_REFUND)
    direct_vm.sender = direct_charlie
    contract.void_market(0)

    assert contract.get_outcome(0) == "invalid"
    assert contract.preview_payout(0, to_hex(direct_alice)) == 2 * GEN
    assert contract.preview_payout(0, to_hex(direct_bob)) == GEN


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

def test_validator_agrees_with_identical_mocks(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    direct_vm.warp(AFTER_RESOLVE)
    contract.resolve(0)

    assert direct_vm.run_validator() is True


def test_resolve_is_rejected_when_validator_disagrees(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    direct_vm.warp(AFTER_RESOLVE)
    assert contract.resolve(0) == "yes"

    # Same page, different answer: the validator recomputes "no" and must
    # reject the leader's "yes".
    direct_vm.clear_mocks()
    mock_resolution(direct_vm, NO_ANSWER)

    assert direct_vm.run_validator() is False


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------

def test_withdraw_without_a_balance_reverts(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("nothing to withdraw"):
        contract.withdraw()


@pytest.mark.skip(
    reason="direct mode has no chain layer: balances and external messages are "
    "not emulated, so the transfer hop can only be checked on Studio or testnet"
)
def test_withdraw_sends_gen(direct_vm, direct_deploy, direct_alice):  # pragma: no cover
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    direct_vm.warp(AFTER_RESOLVE)
    contract.resolve(0)
    contract.claim(0)

    assert contract.withdraw() == GEN
