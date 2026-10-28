"""Thuật toán Hungarian (Jonker-Volgenant) thuần Python.

Tài liệu dự án dùng ``scipy.optimize.linear_sum_assignment``. Máy tính nhà máy là
offline và không cài SciPy, nên module này cung cấp API tương đương
``linear_sum_assignment(cost_matrix) -> (row_ind, col_ind)`` với cùng kết quả tối
ưu, độ phức tạp O(n^2*m). Nếu SciPy có mặt, ``fleet_scheduler`` sẽ dùng SciPy.
"""

from __future__ import annotations

BIG_COST = 1.0e9


def linear_sum_assignment(cost_matrix) -> tuple[list[int], list[int]]:
    """Gán cực tiểu cho ma trận chi phí (hỗ trợ ma trận chữ nhật)."""
    if not cost_matrix:
        return [], []
    matrix = [[float(value) for value in row] for row in cost_matrix]
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if rows == 0 or cols == 0:
        return [], []

    transposed = rows > cols
    if transposed:
        matrix = [list(column) for column in zip(*matrix)]
        rows, cols = cols, rows

    inf = float("inf")
    u = [0.0] * (rows + 1)
    v = [0.0] * (cols + 1)
    p = [0] * (cols + 1)
    way = [0] * (cols + 1)

    for i in range(1, rows + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (cols + 1)
        used = [False] * (cols + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            row = matrix[i0 - 1]
            for j in range(1, cols + 1):
                if used[j]:
                    continue
                current = row[j - 1] - u[i0] - v[j]
                if current < minv[j]:
                    minv[j] = current
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if delta == inf:  # pragma: no cover - chỉ khi ma trận chứa inf
                break
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    pairs = [(p[j] - 1, j - 1) for j in range(1, cols + 1) if p[j]]
    if transposed:
        pairs = [(column, row) for row, column in pairs]
    pairs.sort()
    return [row for row, _ in pairs], [column for _, column in pairs]


def solve_assignment(cost_matrix, penalty: float = BIG_COST) -> tuple[list[tuple[int, int]], float, str]:
    """Trả về (cặp gán khả thi, tổng chi phí, tên solver).

    Chi phí ``>= penalty`` được coi là không khả thi và bị loại khỏi kết quả.
    """
    if not cost_matrix:
        return [], 0.0, "builtin"
    solver = "builtin"
    finite = [[penalty if value >= penalty else float(value) for value in row] for row in cost_matrix]
    try:  # ưu tiên SciPy nếu môi trường có cài đặt
        from scipy.optimize import linear_sum_assignment as scipy_lsa  # type: ignore

        row_ind, col_ind = scipy_lsa(finite)
        solver = "scipy"
    except Exception:
        row_ind, col_ind = linear_sum_assignment(finite)

    pairs = [
        (int(row), int(column))
        for row, column in zip(row_ind, col_ind)
        if finite[row][column] < penalty
    ]
    total_cost = sum(finite[row][column] for row, column in pairs)
    return pairs, total_cost, solver


def greedy_assignment(cost_matrix, penalty: float = BIG_COST) -> list[tuple[int, int]]:
    """Fallback tham lam: chọn cặp rẻ nhất toàn cục trước (dùng khi thiếu solver)."""
    candidates = sorted(
        (
            (float(cost_matrix[row][column]), row, column)
            for row in range(len(cost_matrix))
            for column in range(len(cost_matrix[row]))
            if cost_matrix[row][column] < penalty
        )
    )
    used_rows: set[int] = set()
    used_cols: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _cost, row, column in candidates:
        if row in used_rows or column in used_cols:
            continue
        pairs.append((row, column))
        used_rows.add(row)
        used_cols.add(column)
    return sorted(pairs)
