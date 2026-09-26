from typing import Any


def bar_chart(
    title: str,
    unit: str,
    rows: list[tuple[str, int]]
) -> dict[str, Any]:
    return {
        "type": "bar",
        "title": title,
        "unit": unit,
        "data": [
            {
                "label": label,
                "value": value
            }
            for label, value in rows
        ],
    }