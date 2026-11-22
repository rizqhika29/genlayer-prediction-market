"""Per-method integration tests for every public method, on studionet with the
wallet network.

The flow suite covers the happy path and the guards; this file touches each
remaining method once on the real network, including the GEN transfer in
``withdraw()`` that direct mode cannot simulate.
"""

import json

import pytest

from gltest import get_contract_factory, get_validator_factory
from gltest.assertions import tx_execution_succeeded

CONTRACT = "PredictionMarket"
GEN = 10 ** 18

URL = "https://example.com/"
PAGE = "<html><body><h1>Example Domain</h1></body></html>"

T0 = "2030-01-01T00:00:00Z"
T0_UNIX = 1_893_456_000
RESOLVE_AFTER = T0_UNIX + 3600
REFUND_AFTER = T0_UNIX + 7 * 24 * 3600
AFTER_RESOLUTION = "2030-01-01T02:00:00Z"

QUESTION = "Did the page example.com contain the text Example Domain?"
CRITERIA = "Answer yes if the text Example Domain appears, otherwise no."

YES_ANSWER = json.dumps(
    {
        "outcome": "yes",
        "confidence": 95,
        "source_url": URL,
        "reason": "The page shows the text Example Domain.",
    }
)


@pytest.fixture(scope="module")
def ctx():
    factory = get_validator_factory()
    validators = factory.batch_create_mock_validators(
        count=5,
        mock_llm_response={"nondet_exec_prompt": {"impartial resolver": YES_ANSWER}},
        mock_web_response={
            "nondet_web_request": {URL: {"method": "GET", "status": 200, "body": PAGE}}
        },
    )
    return {
        "validators": [v.to_dict() for v in validators],
        "genvm_datetime": T0,
    }


def at_time(ctx, iso):
    shifted = dict(ctx)
    shifted["genvm_datetime"] = iso
    return shifted


def create_market(ctx, contract):
    tx = contract.create_market(
        args=[QUESTION, CRITERIA, URL, RESOLVE_AFTER, REFUND_AFTER]
    ).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)


def same_address(value, expected):
    return str(value).lower() == expected.lower()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def test_every_view_answers(ctx, default_account):
    contract = get_contract_factory(CONTRACT).deploy(transaction_context=ctx)
    create_market(ctx, contract)

    assert contract.get_market_count().call(transaction_context=ctx) == 1
    assert contract.get_question(args=[0]).call(transaction_context=ctx) == QUESTION
    assert contract.get_criteria(args=[0]).call(transaction_context=ctx) == CRITERIA
    assert contract.get_sources(args=[0]).call(transaction_context=ctx) == URL
    assert same_address(contract.get_creator(args=[0]).call(transaction_context=ctx), default_account.address)
    assert contract.get_outcome(args=[0]).call(transaction_context=ctx) == ""
    assert contract.get_resolution_reason(args=[0]).call(transaction_context=ctx) == ""
    assert contract.get_resolution_source(args=[0]).call(transaction_context=ctx) == ""
    assert contract.get_resolve_after(args=[0]).call(transaction_context=ctx) == RESOLVE_AFTER
    assert contract.get_refund_after(args=[0]).call(transaction_context=ctx) == REFUND_AFTER
    assert contract.get_yes_pool(args=[0]).call(transaction_context=ctx) == 0
    assert contract.get_no_pool(args=[0]).call(transaction_context=ctx) == 0
    assert contract.get_total_pool(args=[0]).call(transaction_context=ctx) == 0
    assert contract.get_now().call(transaction_context=ctx) > 0
    assert contract.get_balance().call(transaction_context=ctx) == 0
    assert contract.get_my_owed().call(transaction_context=ctx) == 0
    assert contract.get_owed(args=[default_account.address]).call(transaction_context=ctx) == 0


def test_stake_and_payout_views(ctx, default_account, accounts):
    contract = get_contract_factory(CONTRACT).deploy(transaction_context=ctx)
    create_market(ctx, contract)

    tx = contract.stake_yes(args=[0]).transact(value=GEN, transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    assert contract.get_my_stake(args=[0]).call(transaction_context=ctx) == GEN
    assert contract.get_my_side(args=[0]).call(transaction_context=ctx) == "yes"
    assert contract.has_claimed(args=[0]).call(transaction_context=ctx) is False
    assert contract.get_stake(args=[0, default_account.address]).call(transaction_context=ctx) == GEN
    assert contract.get_yes_pool(args=[0]).call(transaction_context=ctx) == GEN

    later = at_time(ctx, AFTER_RESOLUTION)
    tx = contract.resolve(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)
    assert contract.get_outcome(args=[0]).call(transaction_context=later) == "yes"
    # The only staker wins the whole pool.
    assert contract.preview_payout(args=[0, default_account.address]).call(
        transaction_context=later
    ) == GEN


# ---------------------------------------------------------------------------
# Writes that the flow suite does not reach
# ---------------------------------------------------------------------------

def test_withdraw_moves_the_gen(ctx, default_account, accounts):
    """The one hop direct mode cannot check: a real GEN transfer."""
    contract = get_contract_factory(CONTRACT).deploy(transaction_context=ctx)
    create_market(ctx, contract)

    tx = contract.stake_yes(args=[0]).transact(value=2 * GEN, transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    later = at_time(ctx, AFTER_RESOLUTION)
    tx = contract.resolve(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)

    tx = contract.claim(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)
    assert contract.get_owed(args=[default_account.address]).call(
        transaction_context=later
    ) == 2 * GEN
    assert contract.has_claimed(args=[0]).call(transaction_context=later) is True

    tx = contract.withdraw().transact(transaction_context=later)
    assert tx_execution_succeeded(tx)

    assert contract.get_owed(args=[default_account.address]).call(
        transaction_context=later
    ) == 0
