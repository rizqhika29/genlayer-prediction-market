"""Integration tests for PredictionMarket on GenLayer Studio (studionet).

These run against a real network: real deploy, real transactions, real consensus
pipeline. The validators are mocked (deterministic web + LLM answers) so the
tests assert the contract's behaviour, not the model's opinion, and they cost no
LLM tokens.

``genvm_datetime`` is pinned per transaction, which makes the betting window and
the resolution time reproducible without sleeping.
"""

import json

import pytest

from gltest import get_contract_factory, get_validator_factory
from gltest.assertions import tx_execution_failed, tx_execution_succeeded

CONTRACT = "PredictionMarket"
GEN = 10 ** 18

URL = "https://example.com/"
PAGE = "<html><body><h1>Example Domain</h1></body></html>"

T0 = "2030-01-01T00:00:00Z"
T0_UNIX = 1_893_456_000                       # 2030-01-01T00:00:00Z
RESOLVE_AFTER = T0_UNIX + 3600                # betting closes here
REFUND_AFTER = T0_UNIX + 7 * 24 * 3600

BEFORE_RESOLUTION = "2030-01-01T00:30:00Z"
AFTER_RESOLUTION = "2030-01-01T02:00:00Z"
AFTER_REFUND = "2030-01-10T00:00:00Z"

YES_ANSWER = json.dumps(
    {
        "outcome": "yes",
        "confidence": 95,
        "source_url": URL,
        "reason": "Halaman memuat teks Example Domain.",
    }
)


@pytest.fixture(scope="module")
def ctx():
    factory = get_validator_factory()
    validators = factory.batch_create_mock_validators(
        count=5,
        mock_llm_response={"nondet_exec_prompt": {"impartial resolver": YES_ANSWER}},
        mock_web_response={
            "nondet_web_request": {
                URL: {"method": "GET", "status": 200, "body": PAGE},
            }
        },
    )
    return {
        "validators": [v.to_dict() for v in validators],
        "genvm_datetime": T0,
    }


def at_time(ctx, iso):
    """Same mocked environment, different pinned transaction datetime."""
    shifted = dict(ctx)
    shifted["genvm_datetime"] = iso
    return shifted


def create_market(ctx, contract):
    tx = contract.create_market(
        args=[
            "Apakah halaman example.com memuat teks Example Domain?",
            "Jawab yes bila teks Example Domain muncul, no bila tidak.",
            URL,
            RESOLVE_AFTER,
            REFUND_AFTER,
        ],
        transaction_context=ctx,
    ).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)


def market_with_stakes(ctx, contract, bettor):
    create_market(ctx, contract)

    tx = contract.stake_yes(args=[0]).transact(value=3 * GEN, transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    tx = contract.stake_no(args=[0], account=bettor).transact(
        value=GEN, transaction_context=ctx
    )
    assert tx_execution_succeeded(tx)


def deploy(ctx):
    return get_contract_factory(CONTRACT).deploy(transaction_context=ctx)


# ---------------------------------------------------------------------------
# End to end: stake, resolve, claim
# ---------------------------------------------------------------------------

def test_market_resolves_and_pays_the_winner(ctx, default_account, accounts):
    contract = deploy(ctx)
    market_with_stakes(ctx, contract, accounts[1])

    assert contract.get_yes_pool(args=[0]).call(transaction_context=ctx) == 3 * GEN
    assert contract.get_no_pool(args=[0]).call(transaction_context=ctx) == GEN

    later = at_time(ctx, AFTER_RESOLUTION)
    tx = contract.resolve(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)

    assert contract.get_outcome(args=[0]).call(transaction_context=later) == "yes"
    assert contract.get_resolution_source(args=[0]).call(transaction_context=later) == URL

    tx = contract.claim(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)

    assert contract.get_owed(args=[default_account.address]).call(
        transaction_context=later
    ) == 4 * GEN
    assert contract.preview_payout(args=[0, default_account.address]).call(
        transaction_context=later
    ) == 4 * GEN


# ---------------------------------------------------------------------------
# Adversarial: lifecycle and caller guards
# ---------------------------------------------------------------------------

def test_betting_closes_at_the_resolution_time(ctx, default_account, accounts):
    contract = deploy(ctx)
    create_market(ctx, contract)

    tx = contract.stake_yes(args=[0]).transact(value=GEN, transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    late = at_time(ctx, AFTER_RESOLUTION)
    tx = contract.stake_yes(args=[0]).transact(value=GEN, transaction_context=late)
    assert tx_execution_failed(tx)

    assert contract.get_yes_pool(args=[0]).call(transaction_context=late) == GEN


def test_resolve_before_the_resolution_time_is_rejected(ctx, default_account, accounts):
    contract = deploy(ctx)
    market_with_stakes(ctx, contract, accounts[1])

    tx = contract.resolve(args=[0]).transact(transaction_context=ctx)
    assert tx_execution_failed(tx)

    assert contract.get_outcome(args=[0]).call(transaction_context=ctx) == ""


def test_void_before_the_refund_window_is_rejected(ctx, default_account, accounts):
    contract = deploy(ctx)
    market_with_stakes(ctx, contract, accounts[1])

    early = at_time(ctx, BEFORE_RESOLUTION)
    tx = contract.void_market(args=[0], account=accounts[2]).transact(
        transaction_context=early
    )
    assert tx_execution_failed(tx)

    assert contract.get_outcome(args=[0]).call(transaction_context=early) == ""


def test_anyone_can_void_after_the_refund_window(ctx, default_account, accounts):
    contract = deploy(ctx)
    market_with_stakes(ctx, contract, accounts[1])

    late = at_time(ctx, AFTER_REFUND)
    tx = contract.void_market(args=[0], account=accounts[2]).transact(
        transaction_context=late
    )
    assert tx_execution_succeeded(tx)

    assert contract.get_outcome(args=[0]).call(transaction_context=late) == "invalid"
    assert contract.preview_payout(args=[0, default_account.address]).call(
        transaction_context=late
    ) == 3 * GEN
    assert contract.preview_payout(args=[0, accounts[1].address]).call(
        transaction_context=late
    ) == GEN


def test_double_claim_is_rejected(ctx, default_account, accounts):
    contract = deploy(ctx)
    market_with_stakes(ctx, contract, accounts[1])

    later = at_time(ctx, AFTER_RESOLUTION)
    tx = contract.resolve(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)

    tx = contract.claim(args=[0]).transact(transaction_context=later)
    assert tx_execution_succeeded(tx)

    tx = contract.claim(args=[0]).transact(transaction_context=later)
    assert tx_execution_failed(tx)

    assert contract.get_owed(args=[default_account.address]).call(
        transaction_context=later
    ) == 4 * GEN
