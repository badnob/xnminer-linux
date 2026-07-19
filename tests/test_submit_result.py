from networking.submit_result import (
    counts_as_reject,
    is_difficulty_mismatch,
    is_xuni_window_reject,
    submit_accepted,
    submit_response_hint,
)


def test_submit_accepted_http_200():
    assert submit_accepted(200, '{"ok":true}') is True


def test_submit_accepted_already_exists_400():
    body = '{"message":"Block already exists, continue"}\n'
    assert submit_accepted(400, body) is True


def test_submit_accepted_already_exists_409():
    assert submit_accepted(409, '{"message":"Key already exists"}') is True


def test_submit_rejected_real_error():
    assert submit_accepted(400, '{"message":"invalid hash"}') is False
    assert submit_accepted(401, "unauthorized") is False


def test_submit_response_hint_duplicate():
    hint = submit_response_hint(400, '{"message":"Block already exists, continue"}')
    assert "duplicate" in hint


def test_difficulty_mismatch_is_not_reject():
    body = (
        '{"message":"Hash does not contain \'m=1100\'. '
        'Your memory_cost setting in your miner is incorrect."}'
    )
    assert is_difficulty_mismatch(401, body) is True
    assert counts_as_reject(401, body) is False
    assert "difficulty" in submit_response_hint(401, body).lower()


def test_xuni_window_is_not_reject():
    body = '{"message":"XUNI Submitted outside of proper time frame."}'
    assert is_xuni_window_reject(400, body) is True
    assert counts_as_reject(400, body) is False


def test_timeout_is_not_reject():
    assert counts_as_reject(0, "timed out") is False
    assert counts_as_reject(0, "") is False


def test_true_pool_error_counts_as_reject():
    assert counts_as_reject(400, '{"message":"invalid hash"}') is True