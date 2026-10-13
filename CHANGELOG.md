# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-10-13

### Added / Changed
- CI workflow running pytest on pushes to main and pull requests (Python 3.10 and 3.12)
- Dependabot config for uv and GitHub Actions (weekly)
- uv.lock for reproducible installs
- .env.example listing environment variables the code reads
- SECURITY.md with private reporting contact
- .gitignore; untracked committed .pytest_cache/ and runstate.egg-info/

### Fixed
- Ledger defaults to `$RUNSTATE_HOME/ledger.db`, so `RUNSTATE_HOME` actually relocates state (#5).
- CLI: no orphaned pending run is left behind when no provider is configured (#6).

