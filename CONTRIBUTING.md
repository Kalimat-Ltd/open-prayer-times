# Contributing

Thanks for contributing to Open Prayer Times.

## Development Setup

1. Create a virtual environment and activate it
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run tests:

```bash
python -m pytest tests/ -q
```

## Code Guidelines

- Keep changes focused and minimal
- Follow existing module boundaries (domain / application / infrastructure / presentation)
- Prefer explicit, readable logic over hidden side effects
- Preserve data format compatibility (`loc.csv`, reference files) unless a migration is provided

## Optimizer Changes

- Any change to Stage 1/2/3 logic should include:
  - targeted tests in `tests/test_multistage_*`
  - validation against representative reference files
  - updated docs in `docs/` when behavior changes

When optimizer parameters or payload contracts change, update:

- `docs/parameter_glossary.md` (canonical term definitions), and
- affected stage/API/data docs (`stage*_optimizer.md`, `api_guide.md`, `data_formats.md`)

## Pull Requests

Please include:
- summary of behavior change
- affected modules
- test commands and outcomes
- documentation updates when applicable

## Reporting Issues

For bug reports, include:
- city and reference file used
- expected vs observed behavior
- reproduction steps
- relevant logs or stack traces

## Reference Data Contributions (High Priority)

Reference-data expansion is currently one of the most impactful contributions.

- Current coverage is limited (roughly 81 countries under `reference/`)
- Adding high-quality datasets for missing countries/cities directly improves practical optimizer coverage

Please include in PRs that add/modify reference data:

1. source/methodology note (for example, provider and method family)
2. formatting compliance with `docs/data_formats.md`
3. city/country list of affected files
4. any known caveats (partial-year data, special local conventions)
