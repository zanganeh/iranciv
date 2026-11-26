"""Regenerate tests/fixtures/hex_cases.json, the golden hex-grid cases shared with web/js/hex.js.

    uv run python tests/make_hex_fixture.py

Only rerun this on purpose (a deliberate change to the grid maths); test_hexgrid
compares the live code against the committed file.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.core.hexgrid import HexGrid  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "hex_cases.json"
GRID = HexGrid(25.0, 12, 9)


def _python_round_cell(grid: HexGrid, x: float, y: float) -> int:
    """cell_at with Python's banker's round(), to find the ties where it differs from JS Math.round."""
    q = (math.sqrt(3) / 3 * x - y / 3) / grid.radius
    r = (2 / 3 * y) / grid.radius
    cy = -q - r
    rx, ry, rz = round(q), round(cy), round(r)
    dx, dy, dz = abs(rx - q), abs(ry - cy), abs(rz - r)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy <= dz:
        rz = -rx - ry
    row, col = rz, rx + (rz - (rz & 1)) // 2
    return grid.index(col, row) if 0 <= col < grid.cols and 0 <= row < grid.rows else -1


def cases() -> dict:
    g = GRID
    points = []
    for i in range(g.size):
        cx, cy = g.center(*g.col_row(i))
        points.append(("center", cx, cy))
        for k in range(6):  # just inside each corner
            a = math.radians(60 * k - 30)
            points.append(("near_corner", cx + 0.9 * g.radius * math.cos(a), cy + 0.9 * g.radius * math.sin(a)))
    # exact rounding ties (cube coordinate exactly n + 0.5), where Python's round() would disagree with JS
    # (they occur on shared edges and corners: a lattice of quarter-width / quarter-radius steps finds them)
    w = math.sqrt(3) * g.radius
    ties = []
    for j in range(-2, 6 * g.rows + 2):
        for i in range(-2, 4 * g.cols + 4):
            x, y = i * w / 4, j * g.radius / 4
            if g.cell_at(x, y) != _python_round_cell(g, x, y) and ("tie", x, y) not in ties:
                ties.append(("tie", x, y))
    points += ties[::max(1, len(ties) // 60)]
    points += [("outside", -100.0, -100.0), ("outside", 1e4, 50.0), ("outside", 50.0, 1e4), ("outside", -30.0, 0.0)]
    return {
        "description": "Golden cases for pipeline/core/hexgrid.py and web/js/hex.js (cellAt uses Math.round = floor(x + 0.5)).",
        "grid": g.to_dict(),
        "cell_at": [{"kind": k, "x": x, "y": y, "cell": g.cell_at(x, y)} for k, x, y in points],
        "center": [{"cell": i, "x": g.center(*g.col_row(i))[0], "y": g.center(*g.col_row(i))[1]} for i in range(g.size)],
        "neighbours": [{"cell": i, "neighbours": g.neighbours(i)} for i in range(g.size)],
    }


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    data = cases()
    FIXTURE.write_text(json.dumps(data, indent=1) + "\n")
    n_ties = sum(c["kind"] == "tie" for c in data["cell_at"])
    print(f"wrote {FIXTURE} ({len(data['cell_at'])} cell_at cases, {n_ties} ties)")
