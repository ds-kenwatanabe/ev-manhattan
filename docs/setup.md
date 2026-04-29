# Setup

This project is a local Python application. The browser UI runs with the standard library HTTP server plus the project dependencies in `requirements.txt`.

## Requirements

- Python 3.11 or newer is recommended.
- A local virtual environment.
- The processed data files in `data/processed/`.

## Install

From the project root:

```bash
cd ev-manhattan
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For a reproducible local setup with the default sample data:

```bash
cp .env.example .env
make setup
```

## Start The Browser UI

```bash
.venv/bin/python src/run/web_app.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Stop Or Restart

Find the running process:

```bash
pgrep -af "src/run/web_app.py"
```

Stop it:

```bash
kill <pid>
```

Start it again:

```bash
.venv/bin/python src/run/web_app.py --host 127.0.0.1 --port 8000
```

## Verify The Install

Compile the main files:

```bash
.venv/bin/python -m py_compile src/run/web_app.py src/solve/rcsp_one_vehicle.py src/viz/overlay_plan.py
```

Check the web app responds:

```bash
.venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000').status)"
```

Expected output:

```text
200
```

## Reproducibility Helpers

The repository includes these project-level helpers:

- `.env.example`: local environment defaults for host, port, seed, and sample size.
- `Makefile`: repeatable setup, test, run, sample generation, and Docker commands.
- `pyproject.toml`: project metadata and dependency groups.
- `Dockerfile`: container image for running the browser app.
- `data/sample/`: small committed sample scenario generated from a seed.

Generate the sample scenario:

```bash
make sample SEED=7
```

Run tests:

```bash
make test
```

Build and run the Docker image:

```bash
make docker-build
make docker-run
```
