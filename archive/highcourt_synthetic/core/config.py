from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_CFG = yaml.safe_load((ROOT / "config" / "judge_rules.yaml").read_text())

JUDGES = _CFG["judges"]
LOCKED = _CFG["locked"]
FILL_TARGET = _CFG["fill_target"]
BALANCE = _CFG["balance"]
HEARING_TYPES = _CFG["hearing_types"]
PREREQ_DAYS = _CFG["prereq_days"]
STAGE_FLOW = _CFG["stage_flow"]

MODEL = yaml.safe_load((ROOT / "config" / "model.yaml").read_text())
OPTIMIZER = yaml.safe_load((ROOT / "config" / "optimizer.yaml").read_text())
CALENDAR = yaml.safe_load((ROOT / "config" / "calendar.yaml").read_text())

URGENT_PURPOSES = {p for p, h in HEARING_TYPES.items() if h["urgent"]}


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: float) -> str:
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def capacity(judge_id: str, blocks=None) -> int:
    blocks = blocks or JUDGES[judge_id]["blocks"]
    return sum(to_min(b["end"]) - to_min(b["start"]) for b in blocks)


def next_stage(purpose: str) -> str:
    if purpose in STAGE_FLOW:
        i = STAGE_FLOW.index(purpose)
        return STAGE_FLOW[min(i + 1, len(STAGE_FLOW) - 1)]
    return "admission"  # urgent matters go on to admission after interim relief
