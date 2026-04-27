# Troubleshooting

## FileNotFoundError For Graph Or Data Files

Run commands from the project root:

```bash
cd ev-manhattan
```

Check that this file exists:

```text
data/processed/manhattan_drive.graphml
```

## Browser UI Does Not Open

Check whether the process is running:

```bash
pgrep -af "src/run/web_app.py"
```

Start it:

```bash
.venv/bin/python src/run/web_app.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Port Already In Use

Find the process:

```bash
pgrep -af "src/run/web_app.py"
```

Stop it:

```bash
kill <pid>
```

Or start on another port:

```bash
.venv/bin/python src/run/web_app.py --host 127.0.0.1 --port 8001
```

## Map Selector Does Not Update Customers

Make sure `Customer selection` is set to:

```text
Choose on map
```

Clicks are ignored when the mode is `Random customers`.

After changing `Available customers`, the generated customer pool changes. Previously selected IDs may no longer refer to the same points.

## Vehicle Does Not Finish Route

If the page says:

```text
Did not complete route in the given time
```

then the planner reached the configured `End time`. Try increasing `End time`, reducing selected customers, increasing charger power, increasing battery size, or increasing `dt` for faster approximate runs.

## Recharging Takes Too Long

Charging consumes route time. Low battery settings can cause multiple recharge stops and may prevent completion inside the time window.

Try higher battery kWh, higher initial SoC, lower reserve kWh, higher minimum charger power, fewer customers, or a longer operating window.

## Raw Outputs

Generated files are written to:

```text
data/outputs/
```

Useful files:

- `plans.json`
- `queue_adjusted_sessions.csv`
- `web_instance_...json`
- `web_prices_...csv`
- `web_plan_...html`
