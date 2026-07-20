from toast.pipeline.confidence import corroborated_confidence, initial_confidence


def test_tier_prior_scales_extractor_score():
    assert initial_confidence(1, 1.0) == 0.98
    assert initial_confidence(5, 1.0) == 0.45
    # lower extractor confidence lowers the result
    assert initial_confidence(4, 0.5) < initial_confidence(4, 0.9)


def test_corroboration_bumps_and_caps():
    base = 0.45
    assert corroborated_confidence(base, 0) == 0.45
    assert corroborated_confidence(base, 2) == 0.61
    assert corroborated_confidence(0.98, 10) == 0.99  # capped
