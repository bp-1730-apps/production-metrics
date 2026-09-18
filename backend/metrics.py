"""
Canonical metric registry for L2L production-summary data.

L2L's weekly and daily reporting endpoints return overlapping but not
identical field sets. The OEE rename ("average_oee" on weekly vs.
"overall_equipment_effectiveness" on daily) is confirmed against this
account's own real sample payloads (see test_api.py's WEEKLY_SAMPLE /
DAILY_SAMPLE, captured from Plant 1730's L2L instance). L2L's own generic
API documentation ("Reporting Method: Production: Weekly/Daily Summary
Data by Line") independently confirms the same weekly/daily split, and
additionally shows its illustrative weekly example naming the
planned-production field "planned_production_downtime_minutes" instead of
daily's "planned_production_minutes" -- this account's real weekly sample
doesn't show that particular field at all, so it's unconfirmed here, but
the alias below is harmless either way (it only fires if that exact raw
key ever shows up) and cheap insurance against the docs' explicit warning
that "fields available will increase over time." This module gives every
metric a single stable ID so the rest of the API -- and the dashboard --
never has to know which raw field name a given granularity uses.

Fields that identify a record (line_id, product, shift, dates, etc.)
rather than measure something are excluded from the metric registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- Raw field name -> canonical metric ID -------------------------------
# Only fields that differ by granularity need an entry here. Every other
# raw field is used as its own canonical ID.
FIELD_ALIASES = {
    "average_oee": "oee",
    "overall_equipment_effectiveness": "oee",
    # Weekly's documented example calls this "planned_production_downtime_
    # minutes"; daily calls it "planned_production_minutes". Canonicalize
    # to the daily name since that's the one already used throughout
    # SUM_FIELDS/KNOWN_METRICS/aggregate_records' weighting.
    "planned_production_downtime_minutes": "planned_production_minutes",
}

# --- Fields present in each raw payload that are identifiers/labels, not
# metrics. These are dropped before a payload is turned into metric values.
NON_METRIC_FIELDS = {
    "area_id", "area", "line_id", "line", "line_categories",
    "product", "product_id", "product_category_id", "product_category_code",
    "products", "shift", "shift_id", "shifts", "shift_start", "site",
    "start_date", "end_date", "date",
}


@dataclass(frozen=True)
class MetricDef:
    id: str
    label: str
    category: str
    unit: str = "number"          # "percent" | "minutes" | "count" | "rate" | "number" | "hours"
    decimals: int = 0
    higher_is_better: Optional[bool] = None
    available_in: tuple = ("day", "week")
    description: str = ""


# --- Known metrics, curated for good labels/grouping. Anything L2L returns
# that ISN'T listed here still shows up (auto-labeled) -- see
# `describe_metric` below -- so the dashboard is never missing a field the
# API can actually see, it just won't have a hand-written label for it yet.
KNOWN_METRICS: dict[str, MetricDef] = {
    "oee": MetricDef("oee", "OEE", "Performance", "percent", 1, True,
        description="Overall Equipment Effectiveness"),
    "operational_availability": MetricDef("operational_availability", "Availability", "Performance", "percent", 1, True),
    "peff": MetricDef("peff", "Performance Efficiency", "Performance", "percent", 1, True),
    "teff": MetricDef("teff", "Target Efficiency", "Performance", "percent", 1, True),
    "run_rate": MetricDef("run_rate", "Run Rate", "Performance", "rate", 1, True),
    "ppp": MetricDef("ppp", "Parts Per Pitch", "Performance", "number", 2, True),

    "actual": MetricDef("actual", "Actual Production", "Production", "count", 0, True),
    "demand": MetricDef("demand", "Demand", "Production", "count", 0, None),
    "average_demand": MetricDef("average_demand", "Average Demand", "Production", "count", 0, None, ("week",)),
    "past_due": MetricDef("past_due", "Past Due", "Production", "count", 0, False),
    "pph": MetricDef("pph", "Parts Per Hour", "Production", "rate", 0, True),
    "ppmh": MetricDef("ppmh", "Parts Per Man-Hour", "Production", "rate", 0, True),
    "theoretical_parts": MetricDef("theoretical_parts", "Theoretical Parts", "Production", "count", 0, None),
    "theoretical_parts_production_time": MetricDef("theoretical_parts_production_time", "Theoretical Parts (Run Time)", "Production", "count", 0, None),
    "npm": MetricDef("npm", "Net Parts per Minute", "Production", "rate", 0, True),
    "tpu": MetricDef("tpu", "Time Per Unit", "Production", "number", 3, False),

    "scrap": MetricDef("scrap", "Scrap", "Quality", "count", 0, False),
    "scrap_percent": MetricDef("scrap_percent", "Scrap %", "Quality", "percent", 1, False),
    "reject_percent": MetricDef("reject_percent", "Reject %", "Quality", "percent", 1, False, ("week",)),
    "yield": MetricDef("yield", "Yield", "Quality", "percent", 1, True),

    "downtime_minutes": MetricDef("downtime_minutes", "Downtime", "Downtime", "minutes", 0, False),
    "nonproduction_minutes": MetricDef("nonproduction_minutes", "Non-Production Time", "Downtime", "minutes", 0, False, ("day",)),
    "changeover_actual": MetricDef("changeover_actual", "Changeovers", "Downtime", "count", 0, None, ("day",)),
    "changeover_duration": MetricDef("changeover_duration", "Changeover Duration", "Downtime", "minutes", 0, False, ("day",)),
    "planned_production_minutes": MetricDef("planned_production_minutes", "Planned Production Time", "Downtime", "minutes", 0, None),

    "operator_count": MetricDef("operator_count", "Operator Count", "Labor", "number", 1, None),
    "earned_hours": MetricDef("earned_hours", "Earned Hours", "Labor", "hours", 1, True),
    "labor_efficiency": MetricDef("labor_efficiency", "Labor Efficiency", "Labor", "percent", 0, True),
    "labor_efficiency_ideal_cycle_time": MetricDef("labor_efficiency_ideal_cycle_time", "Ideal Cycle Time", "Labor", "number", 0, None, ("week",)),
    "labor_efficiency_standard_time": MetricDef("labor_efficiency_standard_time", "Standard Time", "Labor", "number", 0, None, ("week",)),
    "takt_time": MetricDef("takt_time", "Takt Time", "Labor", "number", 0, False, ("day",)),
    "lmpu": MetricDef("lmpu", "Labor Minutes Per Unit", "Labor", "number", 2, False),
    "lhpu": MetricDef("lhpu", "Labor Hours Per Unit", "Labor", "number", 3, False),
    "plmpu": MetricDef("plmpu", "Planned Labor Minutes Per Unit", "Labor", "number", 2, None),

    "pitch_count": MetricDef("pitch_count", "Pitch Count", "Pitch", "count", 0, None),
    "pitch_pace": MetricDef("pitch_pace", "Pitch Pace", "Pitch", "number", 1, None),
}

# --- Aggregation rules for combining multiple daily records (e.g. one per
# shift/product) into a single per-line-per-day row. Anything not listed
# in SUM_FIELDS falls back to a weighted average (weighted by
# planned_production_minutes, falling back to a plain average).
SUM_FIELDS = {
    "actual", "demand", "scrap", "downtime_minutes", "planned_production_minutes",
    "nonproduction_minutes", "earned_hours", "past_due", "pitch_count",
    "theoretical_parts", "theoretical_parts_production_time",
    "changeover_actual", "changeover_duration",
}


def canonical_key(raw_key: str) -> str:
    return FIELD_ALIASES.get(raw_key, raw_key)


def normalize_record(raw: dict) -> dict:
    """Strip identifier fields and rename fields to their canonical metric ID."""
    out = {}
    for k, v in raw.items():
        if k in NON_METRIC_FIELDS or v is None:
            continue
        out[canonical_key(k)] = v
    return out


def describe_metric(metric_id: str) -> MetricDef:
    """Return a MetricDef for any metric ID, auto-labeling ones we haven't
    curated by hand yet so the API never silently hides a field L2L adds."""
    if metric_id in KNOWN_METRICS:
        return KNOWN_METRICS[metric_id]
    label = metric_id.replace("_", " ").title()
    return MetricDef(metric_id, label, "Other", "number", 2, None)


def all_known_metric_ids() -> set:
    return set(KNOWN_METRICS.keys())


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aggregate_records(records: list[dict]) -> dict:
    """Combine multiple normalized records (e.g. one per shift/product for
    the same line-day) into a single row.

    Count-like fields (SUM_FIELDS) are summed. Rate/percentage fields are
    combined as a weighted average, weighted by planned_production_minutes
    where available (falling back to an unweighted average) -- this is a
    reasonable approximation for a dashboard trend line, not a claim that it
    reproduces L2L's own internal OEE formula exactly.
    """
    if not records:
        return {}
    if len(records) == 1:
        return dict(records[0])

    weights = [_to_float(r.get("planned_production_minutes")) or 0.0 for r in records]
    total_weight = sum(weights)

    keys = set()
    for r in records:
        keys.update(r.keys())

    out = {}
    for key in keys:
        values = [_to_float(r.get(key)) for r in records]
        numeric = [v for v in values if v is not None]
        if not numeric:
            # Non-numeric field (shouldn't normally happen post-normalize) --
            # keep the first non-null value we see.
            for r in records:
                if r.get(key) is not None:
                    out[key] = r[key]
                    break
            continue

        if key in SUM_FIELDS:
            out[key] = sum(numeric)
        else:
            if total_weight > 0 and len(numeric) == len(records):
                out[key] = sum(v * w for v, w in zip(numeric, weights)) / total_weight
            else:
                out[key] = sum(numeric) / len(numeric)
    return out
