# Testing

The project uses `pytest`.

## Install Test Dependencies

```bash
.venv/bin/pip install -r requirements.txt
```

## Run The Fast Suite

```bash
.venv/bin/python -m pytest
```

The fast suite covers:

- timeline drive and recharge summaries,
- grouped recharge sessions for UI/map display,
- charger queue waiting behavior,
- vehicle summary formatting,
- charging-aware planner behavior with monkeypatched road distances.

## Run Performance Guardrails

Performance tests are opt-in:

```bash
RUN_PERF_TESTS=1 .venv/bin/python -m pytest -m performance
```

The current performance test avoids the Manhattan graph and checks for route-loop regressions in the Python planner logic.

## Useful Targets

Run only unit-style tests:

```bash
.venv/bin/python -m pytest tests/test_summarize.py tests/test_queues.py
```

Run planner behavior tests:

```bash
.venv/bin/python -m pytest tests/test_planner_greedy_recharge.py
```

Run web summary tests:

```bash
.venv/bin/python -m pytest tests/test_web_summary.py
```
