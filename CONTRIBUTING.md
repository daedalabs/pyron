# Contributing to Pyron

Thank you for your interest in contributing to **Pyron**! We welcome bug reports, documentation improvements, test cases, and feature pull requests.

## Contributor Agreement

By submitting a pull request, patch, or contribution to this repository, you agree that your contribution is subject to the terms of the [Pyron License](LICENSE), including **Section 4 (Contributions and Pull Requests)**:
- You confirm that the contribution is your original work.
- You grant Deogenes Maranan / DaedaLabs a perpetual, irrevocable, worldwide, royalty-free, transferable, and sublicensable license to use, modify, relicense, commercialize, or archive the contribution.
- This allows DaedaLabs to maintain, update, or archive the project and its licenses over time without administrative lock-in.

---

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/daedalabs/pyron.git
   cd pyron
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   # On Windows:
   .venv\Scripts\Activate.ps1
   # On Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies in editable mode:**
   ```bash
   pip install -e ".[dev,pdf]"
   ```

---

## Code Quality Standards

We enforce strict linting, type-safety, and test coverage before merging:

1. **Run the test suite:**
   ```bash
   pytest
   ```
   All tests must pass.

2. **Run the linter / formatter:**
   ```bash
   ruff check .
   ```
   Code must be zero-warning and follow clean idiomatic Python (Python 3.11+).

---

## Commit Guidelines

- Write clear, descriptive commit messages.
- For new features, include corresponding tests under `tests/`.
- Maintain provenance integrity: any changes touching data contracts in `pyron/api/contracts.py` must maintain backward compatibility or be clearly discussed in an issue first.
