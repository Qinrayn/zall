# Contributing to zall

## Design Principles

Before contributing, read `DESIGN.md` (design doc) and `IMPL.md` (implementation principles).

**Key rules:**
- **IPR-0**: Every primitive must have invariant tests with counterexamples
- **IPR-1**: No code without a DESIGN.md section reference
- **IPR-2**: One primitive per commit
- **IPR-3**: No model SDK in `src/zall/core/` (model-agnostic core)
- **IPR-4**: Don't write orchestration before primitives are settled

## Pull Request Process

1. Reference DESIGN.md sections in your PR description
2. Add invariant tests for new primitives (counterexample required)
3. Run full test suite: `pytest tests/`
4. Run IPR-3 check: `python scripts/check_ipr3.py`
5. Run lint: `ruff check src/ tests/`
6. Run mypy: `mypy src/zall/`

## Code Style

- Python 3.10+ with `from __future__ import annotations`
- 100 char line length
- Pydantic v2 for data models
- Protocol classes for interfaces (no ABC)
- Docstrings with DESIGN.md section references

## License

Proprietary — research project.