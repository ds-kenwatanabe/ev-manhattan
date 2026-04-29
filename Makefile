SHELL := /bin/bash
-include .env

PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

HOST ?= $(or $(EV_MANHATTAN_HOST),127.0.0.1)
PORT ?= $(or $(EV_MANHATTAN_PORT),8000)
SEED ?= $(or $(EV_MANHATTAN_SEED),7)
SAMPLE_CUSTOMERS ?= $(or $(EV_MANHATTAN_SAMPLE_CUSTOMERS),12)
SAMPLE_CHARGERS ?= $(or $(EV_MANHATTAN_SAMPLE_CHARGERS),4)
SAMPLE_DATE ?= $(or $(EV_MANHATTAN_SAMPLE_DATE),2025-07-15)
SAMPLE_DIR ?= $(or $(EV_MANHATTAN_SAMPLE_DIR),data/sample)

.PHONY: setup venv install run test test-perf sample compile docker-build docker-run clean-outputs

setup: venv install sample

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run:
	$(PY) src/run/web_app.py --host $(HOST) --port $(PORT)

test:
	$(PY) -m pytest

test-perf:
	RUN_PERF_TESTS=1 $(PY) -m pytest tests/test_performance.py

sample:
	$(PY) src/data/sample_scenario.py --seed $(SEED) --customers $(SAMPLE_CUSTOMERS) --chargers $(SAMPLE_CHARGERS) --date $(SAMPLE_DATE) --out-dir $(SAMPLE_DIR)

compile:
	$(PY) -m py_compile src/run/web_app.py src/solve/rcsp_one_vehicle.py src/viz/overlay_plan.py src/data/sample_scenario.py

docker-build:
	docker build -t ev-manhattan .

docker-run:
	docker run --rm -p $(PORT):8000 ev-manhattan

clean-outputs:
	rm -rf data/cache data/outputs
