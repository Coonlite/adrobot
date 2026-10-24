from dataclasses import dataclass

import pytest

from adrobot.domain import ShareError, split_shares


@dataclass(frozen=True, slots=True)
class Offer:
    id: int
    share: int
    pinned: bool
    status: str


def offer(id: int, share: int = 0, pinned: bool = False, status: str = "synced") -> Offer:
    return Offer(id=id, share=share, pinned=pinned, status=status)


def test_remainder_goes_to_the_lowest_ids():
    assert split_shares([offer(3), offer(1), offer(2)]) == {1: 34, 2: 33, 3: 33}


def test_four_offers_get_25_each():
    assert split_shares([offer(i) for i in (1, 2, 3, 4)]) == {1: 25, 2: 25, 3: 25, 4: 25}


def test_pinned_70_leaves_three_offers_at_10():
    offers = [offer(1, 70, pinned=True), offer(2), offer(3), offer(4)]
    assert split_shares(offers) == {1: 70, 2: 10, 3: 10, 4: 10}


def test_remainder_after_a_pin_is_whole_numbers():
    offers = [offer(1, 70, pinned=True), offer(2), offer(3), offer(4), offer(5)]
    assert split_shares(offers) == {1: 70, 2: 8, 3: 8, 4: 7, 5: 7}
    assert sum(split_shares(offers).values()) == 100


def test_pin_stays_and_removed_is_zero():
    offers = [offer(1, 40, pinned=True), offer(2), offer(3, 25, status="removed")]
    assert split_shares(offers) == {1: 40, 2: 60, 3: 0}


def test_active_shares_sum_to_100():
    offers = [offer(1, 17, pinned=True), offer(2), offer(3), offer(4, status="removed")]
    result = split_shares(offers)
    assert result[4] == 0
    assert result[1] == 17
    assert sum(result.values()) == 100


def test_pending_offer_takes_part_in_the_split():
    assert split_shares([offer(1), offer(2, status="pending")]) == {1: 50, 2: 50}


def test_no_active_offer_gives_all_zero():
    assert split_shares([offer(1, status="removed"), offer(2, status="removed")]) == {1: 0, 2: 0}
    assert split_shares([]) == {}


def test_pin_sum_above_100_raises():
    with pytest.raises(ShareError):
        split_shares([offer(1, 60, pinned=True), offer(2, 50, pinned=True)])


def test_pin_sum_below_100_without_free_offer_raises():
    with pytest.raises(ShareError):
        split_shares([offer(1, 60, pinned=True)])


def test_pins_at_exactly_100_leave_free_offers_at_zero():
    offers = [offer(1, 100, pinned=True), offer(2)]
    assert split_shares(offers) == {1: 100, 2: 0}


def test_removed_pin_is_ignored():
    offers = [offer(1, 90, pinned=True, status="removed"), offer(2)]
    assert split_shares(offers) == {1: 0, 2: 100}


def test_input_is_not_mutated():
    offers = [offer(1), offer(2)]
    before = list(offers)
    split_shares(offers)
    assert offers == before
