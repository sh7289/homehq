"""Lay a recipe's steps out as a Cooking For Engineers style table.

A recipe's steps form a small dependency graph: ingredients are leaves, each
step consumes some mix of ingredients and earlier steps, and the last step is
the finished dish. That graph renders as a table where ingredients run down
the left and each operation spans the rows of everything it consumes,
progressing rightward to the dish.

Rowspans only work if the rows a step covers are contiguous, so the ingredient
order is derived from the graph rather than taken from the recipe file.

Step input lists come from a language model, so nothing here assumes they are
well-formed: unknown names become rows of their own, and cycles terminate.
"""

_MAX_DEPTH = 50


def _index_steps(steps):
    return {step["id"]: step for step in steps or [] if step.get("id")}


def _leaves(step_id, by_id, seen=None):
    """Ingredient names a step consumes, transitively, in encounter order."""
    seen = seen if seen is not None else set()
    if step_id in seen:
        return []  # cycle; the model can emit one
    seen.add(step_id)

    names = []
    for raw in by_id[step_id].get("inputs") or []:
        if raw in by_id:
            names.extend(_leaves(raw, by_id, seen))
        elif raw not in names:
            names.append(raw)
    return names


def _depth(step_id, by_id, seen=None):
    seen = seen if seen is not None else set()
    if step_id in seen or len(seen) > _MAX_DEPTH:
        return 0
    seen = seen | {step_id}
    depths = [
        _depth(raw, by_id, seen) + 1
        for raw in by_id[step_id].get("inputs") or []
        if raw in by_id
    ]
    return max(depths) if depths else 0


def _ordered_names(steps, by_id, declared):
    """Ingredient rows ordered so every step covers a contiguous block."""
    order = []

    def visit(step_id):
        for name in _leaves(step_id, by_id):
            if name not in order:
                order.append(name)

    # Deepest step last: walking it pulls everything in dependency order.
    for step in sorted(steps, key=lambda s: _depth(s["id"], by_id)):
        visit(step["id"])

    # Anything the steps never mention still deserves a row.
    for name in declared:
        if name not in order:
            order.append(name)
    return order


def build_table(ingredients, steps):
    """Return {"columns": int, "rows": [...]} or None when there are no steps."""
    by_id = _index_steps(steps)
    if not by_id:
        return None

    by_name = {i.get("name"): i for i in ingredients or []}
    order = _ordered_names(steps, by_id, list(by_name))
    row_of = {name: index for index, name in enumerate(order)}

    starts = {}
    max_column = -1
    for step in steps:
        if step.get("id") not in by_id:
            continue
        rows = [row_of[name] for name in _leaves(step["id"], by_id) if name in row_of]
        if not rows:
            continue
        start, end = min(rows), max(rows)
        column = _depth(step["id"], by_id)
        max_column = max(max_column, column)
        starts[(start, column)] = {
            "action": step.get("action") or "",
            "column": column,
            "rowspan": end - start + 1,
        }

    columns = max_column + 1
    row_count = len(order)

    # HTML lays cells out left-to-right and ignores our column numbers: a
    # <td> simply takes the next free slot. So every gap has to be filled
    # with an explicit empty cell, or later steps slide left and collide with
    # the spans above them.
    occupied = [[False] * columns for _ in range(row_count)]
    cells_by_row = []
    for row in range(row_count):
        emitted = []
        for column in range(columns):
            if occupied[row][column]:
                continue  # covered by a rowspan from an earlier row
            cell = starts.get((row, column))
            if cell is None:
                occupied[row][column] = True
                emitted.append(
                    {"action": "", "column": column, "rowspan": 1, "spacer": True}
                )
                continue
            for offset in range(cell["rowspan"]):
                occupied[row + offset][column] = True
            emitted.append(cell)
        cells_by_row.append(emitted)

    return {
        "columns": columns,
        "rows": [
            {
                "ingredient": by_name.get(name, {"name": name}),
                "cells": cells_by_row[index],
            }
            for index, name in enumerate(order)
        ],
    }


def method_lines(ingredients, steps):
    """The same graph as a numbered prose method, shallowest step first."""
    by_id = _index_steps(steps)
    if not by_id:
        return []

    ordered = sorted(steps, key=lambda s: _depth(s["id"], by_id))
    labels = {}
    lines = []
    for number, step in enumerate(ordered, start=1):
        parts = []
        for raw in step.get("inputs") or []:
            parts.append(f"step {labels[raw]}" if raw in labels else raw)
        labels[step["id"]] = number
        joined = ", ".join(parts)
        lines.append(f"{number}. {step.get('action') or ''}: {joined}".rstrip(": "))
    return lines
