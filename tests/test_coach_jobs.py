from app.coaching.model_job_manager import estimate_minutes, normalize_options


def test_coach_defaults_are_safe_and_estimated():
    options = normalize_options({"champion": "Diana"})
    assert options["ingest_ladder"] is False
    assert options["timeline_matches"] == 300
    estimate = estimate_minutes(options)
    assert estimate["minutes_low"] >= 2
    assert estimate["minutes_high"] >= estimate["minutes_low"]


def test_large_ladder_ingestion_has_larger_estimate():
    small = normalize_options({"champion": "Diana", "ingest_ladder": True, "target_records": 1000})
    large = normalize_options({"champion": "Diana", "ingest_ladder": True, "target_records": 100000})
    assert estimate_minutes(large)["minutes_high"] > estimate_minutes(small)["minutes_high"]
