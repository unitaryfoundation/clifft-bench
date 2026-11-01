"""Small TOML fixtures without adding a serialization dependency."""

import json
import math


def write_toml(path, document):
    def value(item):
        if isinstance(item, dict):
            return (
                "{ "
                + ", ".join(
                    f"{json.dumps(k)} = {value(v)}" for k, v in item.items() if v is not None
                )
                + " }"
            )
        if isinstance(item, list):
            return "[" + ", ".join(value(v) for v in item) + "]"
        if isinstance(item, float) and not math.isfinite(item):
            return str(item)
        return json.dumps(item)

    path.write_text(
        "\n".join(f"{json.dumps(k)} = {value(v)}" for k, v in document.items() if v is not None)
        + "\n"
    )
