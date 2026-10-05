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


def grouped_bar_chart(
    title: str,
    unit: str,
    series: list[str],
    rows: list[tuple[str, list[int]]]
) -> dict[str, Any]:
    return {
        "type": "grouped_bar",
        "title": title,
        "unit": unit,
        "series": series,
        "data": [
            {"label": label, "values": values}
            for label, values in rows
        ],
    }


def table_chart(
    title: str,
    columns: list[str],
    rows: list[list[Any]]
) -> dict[str, Any]:
    return {
        "type": "table",
        "title": title,
        "columns": columns,
        "rows": rows,
    }