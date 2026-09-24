"""Advocate behaviour model, shared by the synthetic history and the simulator."""
import numpy as np

AGENT_TYPES = ["diligent", "busy", "chronic"]
AGENT_MIX = [0.4, 0.4, 0.2]
BASE_SHOW = {"diligent": 0.9, "busy": 0.6, "chronic": 0.35}
CONFIRM_RATE = {"diligent": 0.85, "busy": 0.6, "chronic": 0.3}
PARTY_IN_PERSON_SHOW = 0.8


def p_show(agent_type, fixed_slot=False, bundled=False, confirmed=False, clashes=0, warned=False):
    p = BASE_SHOW[agent_type]
    p += 0.15 * fixed_slot + 0.10 * bundled + 0.20 * confirmed - 0.10 * clashes
    # A busy advocate who got a costs warning after a confirmed no-show responds to it
    if warned and agent_type == "busy":
        p += 0.15
    return float(np.clip(p, 0.02, 0.98))


PREPARED = {"diligent": 0.9, "busy": 0.7, "chronic": 0.5}


def p_effective_given_heard(prereq_frac, old_unsummarised=False, agent_type=None, confirmed=False):
    """Both sides present: effective depends on whether prerequisites are done, whether
    counsel came prepared (confirming readiness at T-2 means they did the work), and for
    cases 4+ years old, whether a cover sheet brought everyone to the same page."""
    p = 0.1 + 0.8 * prereq_frac
    if agent_type is not None:
        p *= 0.95 if confirmed else PREPARED[agent_type]
    return p * (0.5 if old_unsummarised else 1.0)


def will_confirm(rng, agent_type):
    return rng.random() < CONFIRM_RATE[agent_type]
