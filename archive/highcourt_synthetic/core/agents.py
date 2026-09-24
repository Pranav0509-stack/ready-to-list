"""Advocate behaviour model, shared by the synthetic history, the simulator and the planner.
Every number comes from config/model.yaml."""
import numpy as np

from core.config import MODEL

ADV = MODEL["advocates"]
FX = MODEL["show_effects"]
EFF = MODEL["effective"]

AGENT_TYPES = list(ADV["mix"])
AGENT_MIX = [ADV["mix"][t] for t in AGENT_TYPES]
BASE_SHOW = ADV["base_show"]
CONFIRM_RATE = ADV["confirm_rate"]
PREPARED = ADV["prepared"]
PARTY_IN_PERSON_SHOW = ADV["party_in_person_show"]


def p_show(agent_type, fixed_slot=False, bundled=False, confirmed=False, clashes=0, warned=False):
    p = BASE_SHOW[agent_type]
    p += FX["fixed_slot"] * fixed_slot + FX["bundled"] * bundled + FX["confirmed"] * confirmed
    p += FX["per_clash"] * clashes
    # A busy advocate who got a costs warning after a confirmed no-show responds to it
    if warned and agent_type == "busy":
        p += FX["costs_warning_busy"]
    return float(np.clip(p, FX["floor"], FX["ceiling"]))


def p_effective_given_heard(prereq_frac, old_unsummarised=False, agent_type=None, confirmed=False):
    """Both sides present: effective depends on whether prerequisites are done, whether
    counsel came prepared (confirming readiness at T-2 means they did the work), and for
    cases 4+ years old, whether a cover sheet brought everyone to the same page."""
    p = EFF["base"] + EFF["prereq_slope"] * prereq_frac
    if agent_type is not None:
        p *= ADV["prepared_if_confirmed"] if confirmed else PREPARED[agent_type]
    return p * (EFF["old_unsummarised_factor"] if old_unsummarised else 1.0)


def will_confirm(rng, agent_type):
    return rng.random() < CONFIRM_RATE[agent_type]
