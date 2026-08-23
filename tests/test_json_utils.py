import json

import numpy as np

from app.json_utils import json_safe


def test_json_safe_replaces_non_finite_values_recursively():
    result = json_safe({"inf": float("inf"), "nan": np.float64("nan"), "items": [1, -float("inf")]})
    assert result == {"inf": None, "nan": None, "items": [1, None]}
    assert json.dumps(result, allow_nan=False)
