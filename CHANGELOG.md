# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added / Changed
- CI workflow running pytest on pushes to main and pull requests (Python 3.10 and 3.12)
- Dependabot config for uv and GitHub Actions (weekly)
- uv.lock for reproducible installs
- .env.example listing environment variables the code reads
- SECURITY.md with private reporting contact
- .gitignore; untracked committed .pytest_cache/ and runstate.egg-info/
