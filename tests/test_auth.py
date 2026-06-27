from hermes_a2a_bridge.auth import bearer_is_valid, constant_time_equal


def test_bearer_auth():
    assert not bearer_is_valid(None, "secret")
    assert not bearer_is_valid("Bearer wrong", "secret")
    assert bearer_is_valid("Bearer secret", "secret")


def test_constant_time_helper_results():
    assert constant_time_equal("same", "same")
    assert not constant_time_equal("same", "different")

