from adrobot.domain.presence import campaign_place, index_states


def test_archive_list_wins_and_a_missing_id_is_deleted():
    states = index_states(
        [
            {"id": 1, "state": "active"},
            {"id": 2, "state": "disabled"},
            {"id": 3, "state": "active"},
            {"id": True, "state": "active"},
            "skip",
        ],
        [{"id": 3, "state": "deleted"}, {"id": 4, "name": "functions"}],
    )
    assert campaign_place(states, 1) == "active"
    assert campaign_place(states, 2) == "disabled"
    assert campaign_place(states, 3) == "archived"
    assert campaign_place(states, 4) == "archived"
    assert campaign_place(states, 9) == "deleted"
    assert set(states) == {1, 2, 3, 4}


def test_a_deleted_state_on_the_live_list_is_still_the_archive():
    states = index_states([{"id": 8, "state": "deleted"}], [])
    assert campaign_place(states, 8) == "archived"
