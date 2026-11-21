"""Direct mode tests for contracts/prediction_market.py.

Every web page and LLM answer is mocked, so the tests exercise the
deterministic contract logic plus the leader/validator comparison without
touching the network.

Payouts are pulled: ``claim`` credits a balance inside the contract and
``withdraw`` sends the GEN out. Direct mode has no chain layer, so that last
hop is skipped.
"""

import pytest

from tests.direct.conftest import to_hex

CONTRACT = "contracts/prediction_market.py"
GEN = 10 ** 18

MARKET_URL = "https://example.com/hasil"
MARKET_PAGE = (
    "<html><body><h1>Hasil final</h1>"
    "<p>Indonesia lolos ke putaran final.</p></body></html>"
)

YES_ANSWER = (
    '{"outcome": "yes", "confidence": 92, '
    '"reason": "Halaman menyatakan Indonesia lolos ke putaran final."}'
)
NO_ANSWER = (
    '{"outcome": "no", "confidence": 88, '
    '"reason": "Halaman menyatakan Indonesia tidak lolos."}'
)


def create_market(contract):
    return contract.create_market(
        "Apakah Indonesia lolos ke putaran final?",
        "Jawab yes bila Indonesia bermain di putaran final, no bila tidak.",
        MARKET_URL,
    )


def mock_resolution(direct_vm, llm_answer, page=MARKET_PAGE):
    direct_vm.mock_web(r"example\.com/hasil", {"status": 200, "body": page})
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
    assert contract.get_question(0) == "Apakah Indonesia lolos ke putaran final?"
    assert contract.get_sources(0) == MARKET_URL
    assert contract.get_outcome(0) == ""
    assert contract.get_total_pool(0) == 0
    assert to_hex(contract.get_creator(0)) == to_hex(direct_alice)


def test_create_market_requires_http_source(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("source URL must start with http"):
        contract.create_market("Pertanyaan?", "Kriteria resolusi.", "ftp://example.com/x")


def test_create_market_rejects_more_than_three_sources(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)

    sources = "\n".join("https://example.com/%d" % i for i in range(4))

    with direct_vm.expect_revert("at most 3 source URLs"):
        contract.create_market("Pertanyaan?", "Kriteria resolusi.", sources)


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


def test_cannot_stake_after_resolution(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    contract.resolve(0)

    direct_vm.value = GEN
    with direct_vm.expect_revert("market is already resolved"):
        contract.stake_no(0)
    direct_vm.value = 0


def test_resolve_without_stakes_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

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

    assert contract.resolve(0) == "yes"
    assert contract.get_outcome(0) == "yes"
    assert contract.get_resolution_reason(0) != ""

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


def test_resolve_is_rejected_when_validator_disagrees(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    assert contract.resolve(0) == "yes"

    # Same page, different answer: the validator recomputes "no" and must
    # reject the leader's "yes".
    direct_vm.clear_mocks()
    mock_resolution(direct_vm, NO_ANSWER)

    assert direct_vm.run_validator() is False


def test_validator_agrees_with_identical_mocks(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    contract.resolve(0)

    assert direct_vm.run_validator() is True


def test_void_market_refunds_every_stake(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    stake(direct_vm, contract, "yes", 2 * GEN)

    direct_vm.sender = direct_bob
    stake(direct_vm, contract, "no", GEN)

    direct_vm.sender = direct_alice
    contract.void_market(0)

    assert contract.get_outcome(0) == "invalid"
    assert contract.preview_payout(0, to_hex(direct_alice)) == 2 * GEN
    assert contract.preview_payout(0, to_hex(direct_bob)) == GEN


def test_only_creator_can_void(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)

    direct_vm.sender = direct_bob

    with direct_vm.expect_revert("only the creator can void"):
        contract.void_market(0)


def test_resolve_twice_reverts(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    create_market(contract)
    stake(direct_vm, contract, "yes", GEN)

    mock_resolution(direct_vm, YES_ANSWER)
    contract.resolve(0)

    with direct_vm.expect_revert("market is already resolved"):
        contract.resolve(0)


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
    contract.resolve(0)
    contract.claim(0)

    assert contract.withdraw() == GEN
