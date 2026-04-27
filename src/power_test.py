import pandas as pd, json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

inst = json.load(open(PROCESSED_DIR / "instance_2025-07-15.json"))
ch = pd.DataFrame(inst["chargers"])

print("Chargers total:", len(ch))
print("Power>0:", (ch["power_kw"]>0.1).sum())
print("Power==0 or NaN:", ((ch["power_kw"].isna()) | (ch["power_kw"]<=0.1)).sum())
print("Example rows with zero/missing power:\n", ch[ch["power_kw"].isna() | (ch["power_kw"]<=0.1)].head())
