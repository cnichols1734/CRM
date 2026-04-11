"""
Build and classify Liberty County code profiles for tax protest comparables.

Usage:
    .venv/bin/python3 scripts/classify_liberty_codes.py
    .venv/bin/python3 scripts/classify_liberty_codes.py --min-properties 20
    .venv/bin/python3 scripts/classify_liberty_codes.py --limit 25
    .venv/bin/python3 scripts/classify_liberty_codes.py --refresh
"""
import argparse
import json
import logging
import os
import statistics
import sys
from collections import defaultdict, Counter
from datetime import UTC, datetime
from decimal import Decimal

import openai

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from config import Config
from models import db, LibertyProperty, LibertyCodeProfile

MODEL_CANDIDATES = ["gpt-5.4-mini", "gpt-4.1-mini"]
PROMPT_VERSION = "liberty_code_profiles_v1"
DEFAULT_MIN_PROPERTIES = 5
DEFAULT_SAMPLE_COUNT = 5
VALID_BUCKETS = {
    "platted_subdivision",
    "broad_area",
    "city_bucket",
    "mhp_bucket",
    "unknown",
}
VALID_STRATEGIES = {"normal", "strict", "reject"}

SYSTEM_PROMPT = (
    "You classify Liberty County, Texas appraisal subdivision/abstract codes for a property tax protest tool. "
    "The tool should only use same-code homes as comparable properties when that is reasonably reliable. "
    "Return JSON only. "
    "Pick one bucket from: platted_subdivision, broad_area, city_bucket, mhp_bucket, unknown. "
    "Pick one strategy from: normal, strict, reject. "
    "Use normal only when the code clearly behaves like a true subdivision/neighborhood where same-code homes are likely comparable. "
    "Use strict when the code may still be useful but only with tighter sqft/acreage/zip filters. "
    "Use reject when the code is not a reliable neighborhood comp bucket, such as broad survey/tract/city/special buckets or junk categories. "
    "Return a confidence from 0.0 to 1.0 and a short rationale."
)


def _utcnow_naive():
    """Return a UTC timestamp compatible with existing naive DateTime columns."""
    return datetime.now(UTC).replace(tzinfo=None)


logging.getLogger("httpx").setLevel(logging.WARNING)


def _as_float(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _dedupe_preserve(values):
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _extract_json(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty classification response")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    return json.loads(text[start:end + 1])


def _build_profiles(app):
    rows = []
    with app.app_context():
        query = LibertyProperty.query.with_entities(
            LibertyProperty.abs_subdv_cd,
            LibertyProperty.abs_subdv_desc,
            LibertyProperty.legal_acreage,
            LibertyProperty.situs_num,
            LibertyProperty.sq_ft,
            LibertyProperty.site_addr_1,
            LibertyProperty.legal_desc,
            LibertyProperty.situs_zip,
        )
        rows = query.all()

    grouped = defaultdict(lambda: {
        "desc": None,
        "property_count": 0,
        "acreages": [],
        "with_situs_num": 0,
        "with_sq_ft": 0,
        "zips": Counter(),
        "addresses": [],
        "legals": [],
        "streets": set(),
    })

    for code, desc, acreage, situs_num, sq_ft, site_addr_1, legal_desc, situs_zip in rows:
        if not code:
            continue
        item = grouped[code]
        if not item["desc"] and desc:
            item["desc"] = desc
        item["property_count"] += 1
        acreage_val = _as_float(acreage)
        if acreage_val and acreage_val > 0:
            item["acreages"].append(acreage_val)
        if situs_num:
            item["with_situs_num"] += 1
        if sq_ft and sq_ft > 0:
            item["with_sq_ft"] += 1
        if situs_zip:
            item["zips"][situs_zip] += 1
        if site_addr_1:
            item["addresses"].append(site_addr_1)
            streetish = site_addr_1
            parts = streetish.split()
            if parts and parts[0].isdigit():
                streetish = " ".join(parts[1:])
            item["streets"].add(streetish)
        if legal_desc:
            item["legals"].append(legal_desc)

    profiles = []
    for code, item in grouped.items():
        count = item["property_count"]
        acreages = item["acreages"]
        profiles.append({
            "abs_subdv_cd": code,
            "abs_subdv_desc": item["desc"],
            "property_count": count,
            "avg_acreage": (sum(acreages) / len(acreages)) if acreages else None,
            "median_acreage": statistics.median(acreages) if acreages else None,
            "pct_with_situs_num": (item["with_situs_num"] / count) if count else 0.0,
            "pct_with_sq_ft": (item["with_sq_ft"] / count) if count else 0.0,
            "distinct_street_count": len(item["streets"]),
            "distinct_zip_count": len(item["zips"]),
            "sample_addresses": _dedupe_preserve(item["addresses"])[:DEFAULT_SAMPLE_COUNT],
            "sample_legal_descriptions": _dedupe_preserve(item["legals"])[:DEFAULT_SAMPLE_COUNT],
            "top_zips": [zip_code for zip_code, _ in item["zips"].most_common(3)],
        })

    return profiles


def _manual_profile_decision(profile):
    code = profile["abs_subdv_cd"]
    desc = (profile["abs_subdv_desc"] or "").strip().upper()
    if not code:
        return {
            "bucket": "unknown",
            "strategy": "reject",
            "confidence": 1.0,
            "rationale": "Blank Liberty code cannot support reliable same-code comparables.",
            "model_name": "manual",
        }
    if code == "MHP000" or desc == "NO MHP":
        return {
            "bucket": "mhp_bucket",
            "strategy": "reject",
            "confidence": 1.0,
            "rationale": "NO MHP is not a neighborhood/subdivision code for same-code home comparables.",
            "model_name": "manual",
        }
    return None


def _classify_with_llm(client, profile):
    prompt = {
        "code": profile["abs_subdv_cd"],
        "description": profile["abs_subdv_desc"],
        "property_count": profile["property_count"],
        "avg_acreage": profile["avg_acreage"],
        "median_acreage": profile["median_acreage"],
        "pct_with_situs_num": round(profile["pct_with_situs_num"], 4),
        "pct_with_sq_ft": round(profile["pct_with_sq_ft"], 4),
        "distinct_street_count": profile["distinct_street_count"],
        "distinct_zip_count": profile["distinct_zip_count"],
        "top_zips": profile["top_zips"],
        "sample_addresses": profile["sample_addresses"],
        "sample_legal_descriptions": profile["sample_legal_descriptions"],
    }

    user_prompt = (
        "Classify this Liberty County appraisal code for same-code home comparable reliability.\n"
        "Return JSON with keys: bucket, strategy, confidence, rationale.\n\n"
        f"{json.dumps(prompt, ensure_ascii=True)}"
    )

    last_error = None
    for model_name in MODEL_CANDIDATES:
        for _ in range(3):
            try:
                if model_name.startswith("gpt-5"):
                    response = client.responses.create(
                        model=model_name,
                        instructions=SYSTEM_PROMPT,
                        input=user_prompt,
                        reasoning={"effort": "low"},
                    )
                    output_text = response.output_text
                else:
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.0,
                        response_format={"type": "json_object"},
                    )
                    output_text = response.choices[0].message.content

                parsed = _extract_json(output_text)
                bucket = parsed.get("bucket")
                strategy = parsed.get("strategy")
                confidence = float(parsed.get("confidence"))
                rationale = (parsed.get("rationale") or "").strip()
                if bucket not in VALID_BUCKETS:
                    raise ValueError(f"Invalid bucket: {bucket}")
                if strategy not in VALID_STRATEGIES:
                    raise ValueError(f"Invalid strategy: {strategy}")
                confidence = max(0.0, min(1.0, confidence))
                if not rationale:
                    raise ValueError("Missing rationale")
                return {
                    "bucket": bucket,
                    "strategy": strategy,
                    "confidence": confidence,
                    "rationale": rationale,
                    "model_name": model_name,
                }
            except Exception as exc:
                last_error = exc
                continue
    raise last_error


def _upsert_profile(profile, classification):
    existing = LibertyCodeProfile.query.filter_by(abs_subdv_cd=profile["abs_subdv_cd"]).first()
    if not existing:
        existing = LibertyCodeProfile(abs_subdv_cd=profile["abs_subdv_cd"])
        db.session.add(existing)

    existing.abs_subdv_desc = profile["abs_subdv_desc"]
    existing.property_count = profile["property_count"]
    existing.avg_acreage = profile["avg_acreage"]
    existing.median_acreage = profile["median_acreage"]
    existing.pct_with_situs_num = profile["pct_with_situs_num"]
    existing.pct_with_sq_ft = profile["pct_with_sq_ft"]
    existing.distinct_street_count = profile["distinct_street_count"]
    existing.distinct_zip_count = profile["distinct_zip_count"]
    existing.sample_addresses = profile["sample_addresses"]
    existing.sample_legal_descriptions = profile["sample_legal_descriptions"]
    existing.bucket = classification["bucket"]
    existing.strategy = classification["strategy"]
    existing.confidence = classification["confidence"]
    existing.rationale = classification["rationale"]
    existing.model_name = classification["model_name"]
    existing.prompt_version = PROMPT_VERSION
    existing.classified_at = _utcnow_naive()
    existing.updated_at = _utcnow_naive()


def run(app, min_properties=DEFAULT_MIN_PROPERTIES, limit=None, refresh=False):
    profiles = _build_profiles(app)
    profiles = [p for p in profiles if p["property_count"] >= min_properties]
    profiles.sort(key=lambda p: (-p["property_count"], p["abs_subdv_cd"]))

    key = Config.OPENAI_API_KEY
    if not key:
        raise ValueError("OPENAI_API_KEY is not configured")
    client = openai.OpenAI(api_key=key)

    processed = 0
    llm_calls = 0
    skipped_existing = 0

    with app.app_context():
        for profile in profiles:
            existing = LibertyCodeProfile.query.filter_by(abs_subdv_cd=profile["abs_subdv_cd"]).first()
            if existing and not refresh and existing.prompt_version == PROMPT_VERSION and existing.strategy:
                skipped_existing += 1
                continue

            classification = _manual_profile_decision(profile)
            if not classification:
                classification = _classify_with_llm(client, profile)
                llm_calls += 1

            _upsert_profile(profile, classification)
            processed += 1

            if processed % 25 == 0:
                db.session.commit()
                print(f"Processed {processed} profiles ({llm_calls} LLM calls, {skipped_existing} skipped)")

            if limit and processed >= limit:
                break

        db.session.commit()

    print(
        f"Done. Processed {processed} profiles, made {llm_calls} LLM calls, "
        f"skipped {skipped_existing} existing profiles."
    )


def main():
    parser = argparse.ArgumentParser(description="Classify Liberty County code profiles")
    parser.add_argument("--min-properties", type=int, default=DEFAULT_MIN_PROPERTIES)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    app = create_app()
    run(app, min_properties=args.min_properties, limit=args.limit, refresh=args.refresh)


if __name__ == "__main__":
    main()
