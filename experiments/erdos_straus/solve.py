"""Erdős–Straus conjecture solver.

Conjecture: For every integer n >= 2, there exist positive integers x, y, z
such that  4/n = 1/x + 1/y + 1/z.

This module searches for such a decomposition for a given n using a bounded,
pruned enumeration. Pure Python (no external deps) for maximal reproducibility.

Search strategy (standard, see e.g. Schinzel / Mordell):
  1 <= x <= y <= z is NOT required; instead we exploit:
    4/n = 1/x + 1/y + 1/z  =>  1/x < 4/n  =>  x > n/4  =>  x >= floor(n/4)+1.
  Also 1/x >= 4/n - 1/1 is trivial; the real bound is x in [ceil(n/4)+1, n]
  (since 1/x >= 1/n would force 1/x = 4/n - rest <= 4/n, so x >= n/4; and
  x <= n because otherwise 1/x < 1/n and the remaining 1/y+1/z must exceed
  3/n which forces small denominators captured by the x<=n range with the
  y/z sub-search).

  For each candidate x, we then solve 4/n - 1/x = (4x-n)/(nx) = 1/y + 1/z,
  i.e. find y, z positive with 1/y + 1/z = R where R = (4x-n)/(nx).
  Standard two-variable Egyptian approach: pick y in [ceil(1/R)+1 ...],
  then z = 1/(R - 1/y) and check it's a positive integer.

This is a VERIFY/SEARCH routine, not a proof. It either finds a decomposition
or reports none within the bounded search (a negative result for that n,
within the search bounds -- NOT a proof that n is a counterexample).
"""

from __future__ import annotations

from math import gcd


def solve(n: int, *, y_bound_factor: int = 4) -> tuple[int, int, int] | None:
    """Search for a decomposition 4/n = 1/x + 1/y + 1/z for the given n >= 2.

    Returns (x, y, z) positive integers if found, else None (bounded search
    exhausted -- not a proof of nonexistence).

    y_bound_factor controls how far the inner y-search extends relative to
    the natural bound; larger = more thorough but slower.
    """
    if n < 2:
        return None

    # x range: x must satisfy 1/x < 4/n  =>  x > n/4, and x <= n is a safe
    # upper bound (beyond n, 1/x < 1/n and decomposition tends to require
    # even smaller terms already covered). Try x from ceil(n/4)+1 up to n.
    x_lo = n // 4 + 1
    x_hi = n  # inclusive upper bound for x

    for x in range(x_lo, x_hi + 1):
        # R = 4/n - 1/x = (4x - n) / (n*x). Need R > 0  =>  4x > n  (holds since x>n/4).
        num = 4 * x - n
        den = n * x
        if num <= 0:
            continue
        # R = num/den. We need 1/y + 1/z = num/den with y, z positive ints.
        # For a fixed y: 1/z = num/den - 1/y = (num*y - den)/(den*y)
        #   => z = den*y / (num*y - den), must be a positive integer.
        # y must satisfy 1/y < R  =>  y > den/num, i.e. y >= den//num + 1.
        y_lo = den // num + 1
        # Upper bound for y: 1/y >= R - 1/1 is loose; use that 1/y > R/2 once
        # we demand 1/z positive (since 1/z = R - 1/y > 0 => 1/y < R, already
        # enforced). A practical bound: y <= den (since 1/y >= 1/den would
        # imply R - 1/y <= num/den - 1/den = (num-1)/den; still fine). We cap
        # y at a multiple of den//num to keep search bounded but thorough.
        y_hi = max(y_lo, y_bound_factor * den // num)
        # Also cap y_hi so the search stays tractable for large n.
        if y_hi - y_lo > 2_000_000:
            y_hi = y_lo + 2_000_000

        for y in range(y_lo, y_hi + 1):
            denom_z = num * y - den
            if denom_z <= 0:
                continue
            numer_z = den * y
            if numer_z % denom_z == 0:
                z = numer_z // denom_z
                if z > 0:
                    # Verify (defensive; cheap).
                    if _check(n, x, y, z):
                        return (x, y, z)
    return None


def _check(n: int, x: int, y: int, z: int) -> bool:
    """Verify 4/n == 1/x + 1/y + 1/z exactly (integer arithmetic)."""
    # 4/n == 1/x+1/y+1/z  <=>  4*x*y*z == n*(y*z + x*z + x*y)
    lhs = 4 * x * y * z
    rhs = n * (y * z + x * z + x * y)
    return lhs == rhs


def verify(n: int, decomp: tuple[int, int, int]) -> bool:
    """Public verifier: check a claimed decomposition for n."""
    x, y, z = decomp
    if x <= 0 or y <= 0 or z <= 0:
        return False
    return _check(n, x, y, z)


def reduced_form(n: int, decomp: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return the decomposition sorted ascending and reduced by common gcd.
    Useful for canonical comparison of solutions across runs.
    """
    x, y, z = decomp
    g = gcd(gcd(x, y), z)
    rx, ry, rz = x // g, y // g, z // g
    return tuple(sorted((rx, ry, rz)))  # type: ignore[return-value]


if __name__ == "__main__":  # pragma: no cover
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    res = solve(n)
    if res is None:
        print(f"n={n}: no decomposition found within search bounds (NOT a proof)")
    else:
        ok = verify(n, res)
        print(f"n={n}: 4/{n} = 1/{res[0]} + 1/{res[1]} + 1/{res[2]}  (verified={ok})")
