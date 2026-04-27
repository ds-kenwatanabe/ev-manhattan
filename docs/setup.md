# Setup

This project is a local Python application. The browser UI runs with the standard library HTTP server plus the project dependencies in `requirements.txt`.

## Requirements

- Python 3.11 or newer is recommended.
- A local virtual environment.
- The processed data files in `data/processed/`.

## Install

From the project root:

```bash
cd /home/chris/PycharmProjects/ev-manhattan
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
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
