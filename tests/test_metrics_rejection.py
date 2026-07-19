from monitoring.metrics import MetricsTracker


def test_rejected_flush_counts_unique_blocks_only():
    m = MetricsTracker()
    key = "abc" * 21 + "d"
    for _ in range(12):
        m.record_rejected_flush("XUNI", key)
    assert m.stats.rejected_flush_xuni == 1


def test_rejected_live_counts_unique_blocks_only():
    m = MetricsTracker()
    m.record_rejected_live("XNM", "k1")
    m.record_rejected_live("XNM", "k1")
    m.record_rejected_live("XNM", "k2")
    assert m.stats.rejected_live_xnm == 2
    assert m.stats.failed_live_xnm == 2