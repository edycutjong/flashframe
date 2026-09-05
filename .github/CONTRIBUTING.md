# Contributing to Flashframe

Thank you for contributing! Here is a guide on how to help out.

## How to Contribute

1. **Fork and Branch:** Fork the repository and create a branch from `main`. Use `feat/...` for new features or `fix/...` for bug fixes.
2. **Setup and Install:** We use `uv` for dependency management.
   ```bash
   uv pip install -e . -r requirements.txt
   ```
3. **Run the Application:**
   ```bash
   python -m flashframe.cli
   ```

## Pull Request Requirements

Before opening a pull request, verify that the following checks pass:
- **Tests:** `pytest` must be green.
- **Linting:** `ruff` must be clean.
- **Coverage:** Statement coverage must remain at 100%.

## Commit Conventions

The existing 124 commits in this repository follow Conventional Commits. Please continue this convention for your commit subjects:
- `feat:` for new features
- `fix:` for bug fixes
- `docs:` for documentation changes
- `test:` for adding or updating tests
- `chore:` for maintenance or tooling changes

## What Not to Send

- **No Credentials:** Never commit credentials, and do not include a `.env` file.
- **No Found Footage:** All seed media in this project is synthetic `ffmpeg lavfi` output and must stay that way. Do not commit external video clips.

## Issues

- **Bug Reports:** If you find a bug, please use the [bug report issue template](ISSUE_TEMPLATE/bug_report.md).
- **Feature Requests:** For new features, please use the [feature request issue template](ISSUE_TEMPLATE/feature_request.md).
