# Contributing to zall

Thank you for your interest in contributing to zall! This document outlines the contribution workflow and design principles.

## Design Principles

Before contributing, read `DESIGN.md` and `IMPL.md` for the full design rationale.

**Key rules:**
- **IPR-0**: Every primitive must have invariant tests with counterexamples
- **IPR-1**: No code without a DESIGN.md section reference
- **IPR-2**: One primitive per commit
- **IPR-3**: No model SDK in `src/zall/core/` (model-agnostic core)
- **IPR-4**: Don't write orchestration before primitives are settled

## Getting Started

```bash
# Clone and install
git clone https://github.com/Qinrayn/zall.git
cd zall
pip install -e ".[dev,bs4,images]"

# Run tests
python -m pytest tests/ -q

# Type check
mypy src/zall/ --ignore-missing-imports

# Lint
ruff check src/

# Check IPR-3 compliance
python scripts/check_ipr3.py
```

## Pull Request Process

1. **Fork and branch**: Create a feature branch from `master`
2. **Reference DESIGN.md**: Link relevant sections in your PR description
3. **Add tests**: Include invariant tests with counterexamples for new primitives
4. **Run checks**:
   ```bash
   pytest tests/ -q --tb=short
   mypy src/zall/ --ignore-missing-imports
   ruff check src/ tests/
   python scripts/check_ipr3.py
   ```
5. **Submit**: Open a PR with a clear title and description

## Code Style

- Python 3.10+ with `from __future__ import annotations`
- 100 char line length (enforced by ruff)
- Pydantic v2 for data models (frozen=True where possible)
- Protocol classes for interfaces (no ABC/abstract base classes)
- Docstrings with DESIGN.md section references

## Commit Convention

```
<type>: <description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`

Examples:
- `feat: add LSP go-to-definition tool`
- `fix: correct read_file truncation notice for large files`
- `test: add counterexample for empty rule match`

## Testing Philosophy

zall follows **IPR-0**: every invariant must have a counterexample test.
Tests are organized by component in `tests/`:

```
tests/
├── test_loop_invariants.py
├── test_gate_invariants.py
├── test_verifiability_invariants.py
└── ...
```

Each test file covers:
- **Happy path**: The invariant holds
- **Counterexample**: A case where the invariant would be violated (ensures the guard works)

## Security Issues

Please do not open public issues for security vulnerabilities.
See [SECURITY.md](../SECURITY.md) for the disclosure process.

## License

[MIT](../LICENSE) © 2026 zall contributors