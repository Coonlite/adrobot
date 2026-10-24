from tests.fakes import SETTINGS


def test_api_key_is_not_in_settings_repr():
    assert "secret" not in repr(SETTINGS)
