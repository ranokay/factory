import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from promotion_consolidate_run_test import DryRun, LiveRun, HandoffOrder, Policy, Proposals, TrackingLookup, PromotionCap, MissingField, FetchAndGate  # noqa: E402 -- scripts/ must be on sys.path first
from promotion_consolidate_rules_test import Refused, StrictPlan, ClosedMembers, WorkflowNames, GateBounding, Occurrences  # noqa: E402 -- scripts/ must be on sys.path first
from promotion_consolidate_trust_test import TrustedMarkers, CompletionAndReshapes  # noqa: E402 -- scripts/ must be on sys.path first


__all__ = [
    "DryRun",
    "LiveRun",
    "HandoffOrder",
    "Policy",
    "Proposals",
    "TrackingLookup",
    "PromotionCap",
    "MissingField",
    "FetchAndGate",
    "Refused",
    "StrictPlan",
    "ClosedMembers",
    "WorkflowNames",
    "GateBounding",
    "Occurrences",
    "TrustedMarkers",
    "CompletionAndReshapes",
]


if __name__ == "__main__":
    unittest.main()
