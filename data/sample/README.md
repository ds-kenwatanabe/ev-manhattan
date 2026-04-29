# Sample Data

This directory contains a small deterministic scenario for reproducibility checks.

Regenerate it with:

```bash
make sample SEED=7
```

The sample dataset is intentionally lightweight. It does not replace the full Manhattan processed data under `data/processed/`; it gives tests, examples, and Docker builds a stable scenario that can be recreated from a seed.
