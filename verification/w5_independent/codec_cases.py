"""Prewritten W3/W5 strict wire-codec input classes, unrelated to runtime output."""
from __future__ import annotations
from copy import deepcopy


def valid():
    integers = [-9223372036854775808, -9007199254740993, -1, 0, 9007199254740993, 9223372036854775807]
    texts = ["雪", "NUL\0exact", "e\u0301", "😀" * 80, "<b>literal</b>", ""]
    for index, number in enumerate(integers):
        yield {"number": str(number), "text": texts[index], "active": index % 2 == 0,
               "amount": {"tag": "Decimal", "value": str(number)},
               "timestamp": {"tag": "Timestamp", "value": str(number)},
               "duration": {"tag": "Duration", "value": str(number)},
               "workspace": {"tag": "workspace", "value": "red"},
               "optional": {"tag": "None"} if index % 2 == 0 else {"tag": "Some", "value": "optional 雪\0"},
               "status": {"tag": "Ready", "fields": []} if index % 2 == 0 else {"tag": "Detail", "fields": ["detail 雪"]}}


def invalid():
    original = next(valid())
    mutations = [
        ("integer-json-number", lambda value: value.update(number=1)),
        ("integer-fraction", lambda value: value.update(number="1.5")),
        ("integer-leading-zero", lambda value: value.update(number="01")),
        ("integer-negative-zero", lambda value: value.update(number="-0")),
        ("integer-plus", lambda value: value.update(number="+1")),
        ("integer-overflow", lambda value: value.update(number="9223372036854775808")),
        ("integer-underflow", lambda value: value.update(number="-9223372036854775809")),
        ("unknown-record-field", lambda value: value.update(principal={"username": "alice", "role": "Admin"})),
        ("missing-record-field", lambda value: value.pop("active")),
        ("string-over-scalar-bound", lambda value: value.update(text="😀" * 81)),
        ("lone-high-surrogate", lambda value: value.update(text="\ud800")),
        ("lone-low-surrogate", lambda value: value.update(text="\udfff")),
        ("id-wrong-nominal-tag", lambda value: value["workspace"].update(tag="user")),
        ("id-invalid-value", lambda value: value["workspace"].update(value="red/../../blue")),
        ("option-is-not-null", lambda value: value.update(optional=None)),
        ("option-payload-required", lambda value: value.update(optional={"tag": "Some"})),
        ("option-unknown-field", lambda value: value.update(optional={"tag": "None", "value": "hidden"})),
        ("enum-unknown-tag", lambda value: value["status"].update(tag="Admin")),
        ("enum-unit-has-no-fields", lambda value: value["status"].update(fields=["hidden"])),
        ("decimal-number-coercion", lambda value: value["amount"].update(value=1)),
        ("timestamp-wrong-tag", lambda value: value["timestamp"].update(tag="Decimal")),
        ("duration-unknown-field", lambda value: value["duration"].update(principal="Admin")),
        ("bool-string-coercion", lambda value: value.update(active="true")),
    ]
    for name, mutation in mutations:
        value = deepcopy(original)
        mutation(value)
        yield name, value
