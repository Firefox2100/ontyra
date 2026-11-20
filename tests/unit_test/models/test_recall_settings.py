"""The filler-gate settings reject values that would silently disable or flood the gate."""

import pytest
from pydantic import ValidationError

from ontyra.models.recall import RecallSettings


def test_the_filler_threshold_is_a_probability_and_the_word_limit_positive() -> None:
    assert RecallSettings(filler_threshold=0.7, filler_max_words=8).filler_gate is False
    for bad in ({"filler_threshold": 7}, {"filler_threshold": -0.1}, {"filler_max_words": 0}):
        with pytest.raises(ValidationError):
            RecallSettings.model_validate(bad)
