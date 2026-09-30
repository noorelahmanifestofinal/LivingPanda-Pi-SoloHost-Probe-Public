import hashlib
import json
import statistics


QUALITY_SCORE = {"low": 1, "medium": 2, "high": 3}
CONFIDENCE_SCORE = {"low": 1, "medium": 2, "high": 3}
RESOLVED_OUTCOMES = {"verified_stable", "recurred"}
CAUSATION_NOTE = (
    "Observed local association only; this does not prove the recommendation "
    "caused recovery."
)


def _loads(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def make_playbook_key(cause, category, recommendation_text):
    payload = f"{cause}\x1f{category}\x1f{recommendation_text}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{cause}:{category}:{digest}"


def _percentile(values, q):
    values = sorted(float(v) for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    fraction = pos - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _observation_from_incident(row):
    if row["status"] != "closed":
        return None
    recovery_status = row["recovery_status"]
    if recovery_status not in RESOLVED_OUTCOMES:
        return None
    cause = row["root_cause"]
    category = row["category"]
    action = row["recovery_action"]
    evidence_detail = _loads(row["root_cause_evidence_json"], {})
    evidence = evidence_detail.get("evidence") or []
    if not cause or not category or not action or not evidence:
        return None

    outcome_detail = _loads(row["recovery_outcome_json"], {})
    if outcome_detail.get("result") != recovery_status:
        return None
    components = _loads(row["component_recovery_json"], {})
    recovery_seconds = components.get("incident_recovery_seconds")
    if recovery_status == "verified_stable":
        stability_seconds = outcome_detail.get("stable_window_seconds")
    else:
        stability_seconds = outcome_detail.get("seconds_after_recovery")
    observed_ts = row["recovery_verified_ts"] or row["closed_ts"]
    if observed_ts is None:
        return None
    return {
        "incident_id": row["id"],
        "playbook_key": make_playbook_key(cause, category, action),
        "cause": cause,
        "incident_category": category,
        "recommendation_text": action,
        "outcome": recovery_status,
        "recovery_seconds": recovery_seconds,
        "stability_seconds": stability_seconds,
        "confidence": row["root_cause_confidence"] or "low",
        "evidence_count": len(evidence),
        "observed_ts": int(observed_ts),
    }


def _quality_label(observations):

    if not observations:
        return "low"
    confidence_points = [
        CONFIDENCE_SCORE.get(item["confidence"], 0) for item in observations
    ]
    evidence_counts = [item["evidence_count"] for item in observations]
    avg_confidence = sum(confidence_points) / len(confidence_points)
    avg_evidence = sum(evidence_counts) / len(evidence_counts)
    if avg_confidence >= 2.5 and avg_evidence >= 2:
        return "high"
    if avg_confidence >= 1.5 and avg_evidence >= 1:
        return "medium"
    return "low"


def refresh_playbooks(conn, now_ts):
    rows = conn.execute(
        """
        SELECT *
        FROM incidents
        WHERE status='closed'
          AND recovery_status IN ('verified_stable','recurred')
          AND root_cause IS NOT NULL
          AND recovery_action IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()

    for row in rows:
        item = _observation_from_incident(row)
        if item is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO playbook_observations(
                incident_id, playbook_key, cause, incident_category,
                recommendation_text, outcome, recovery_seconds,
                stability_seconds, confidence, evidence_count, observed_ts
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item["incident_id"], item["playbook_key"], item["cause"],
                item["incident_category"], item["recommendation_text"],
                item["outcome"], item["recovery_seconds"],
                item["stability_seconds"], item["confidence"],
                item["evidence_count"], item["observed_ts"],
            ),
        )

    observation_rows = conn.execute(
        "SELECT * FROM playbook_observations ORDER BY observed_ts ASC, incident_id ASC"
    ).fetchall()
    grouped = {}
    for row in observation_rows:
        grouped.setdefault(row["playbook_key"], []).append(dict(row))

    for key, items in grouped.items():
        stable = sum(1 for item in items if item["outcome"] == "verified_stable")
        recurred = sum(1 for item in items if item["outcome"] == "recurred")
        count = len(items)
        recoveries = [item["recovery_seconds"] for item in items if item["recovery_seconds"] is not None]
        stabilities = [item["stability_seconds"] for item in items if item["stability_seconds"] is not None]
        confidence_distribution = {}
        for item in items:
            confidence = item["confidence"] or "unknown"
            confidence_distribution[confidence] = confidence_distribution.get(confidence, 0) + 1
        quality = _quality_label(items)
        first = items[0]
        conn.execute(
            """
            INSERT INTO playbooks(
                playbook_key, cause, incident_category, recommendation_text,
                observation_count, verified_stable_count, recurrence_count,
                success_rate, median_recovery_seconds, p95_recovery_seconds,
                median_stability_seconds, confidence_distribution,
                evidence_quality, last_updated_ts
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(playbook_key) DO UPDATE SET
                observation_count=excluded.observation_count,
                verified_stable_count=excluded.verified_stable_count,
                recurrence_count=excluded.recurrence_count,

                success_rate=excluded.success_rate,
                median_recovery_seconds=excluded.median_recovery_seconds,
                p95_recovery_seconds=excluded.p95_recovery_seconds,
                median_stability_seconds=excluded.median_stability_seconds,
                confidence_distribution=excluded.confidence_distribution,
                evidence_quality=excluded.evidence_quality,
                last_updated_ts=excluded.last_updated_ts
            """,
            (
                key, first["cause"], first["incident_category"],
                first["recommendation_text"], count, stable, recurred,
                round(stable / count, 4) if count else 0.0,
                round(statistics.median(recoveries), 1) if recoveries else None,
                round(_percentile(recoveries, 0.95), 1) if recoveries else None,
                round(statistics.median(stabilities), 1) if stabilities else None,
                json.dumps(confidence_distribution, separators=(",", ":")),
                quality, int(now_ts),
            ),
        )


def row_to_dict(row, min_observations):
    item = dict(row)
    item["confidence_distribution"] = _loads(
        item.get("confidence_distribution"), {}
    )
    item["learned"] = item["observation_count"] >= min_observations
    item["minimum_observations"] = min_observations

    item["success_rate_pct"] = round(float(item["success_rate"]) * 100, 1)
    item["causation_note"] = CAUSATION_NOTE
    return item


def list_playbooks(
    conn,
    min_observations,
    limit=50,
    cause=None,
    category=None,
    learned_only=False,
):
    limit = max(1, min(200, int(limit)))
    sql = "SELECT * FROM playbooks"
    clauses = []
    params = []
    if cause:
        clauses.append("cause=?")
        params.append(cause)
    if category:
        clauses.append("incident_category=?")
        params.append(category)
    if learned_only:
        clauses.append("observation_count>=?")
        params.append(int(min_observations))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY success_rate DESC, observation_count DESC, last_updated_ts DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(row, min_observations) for row in rows]


def _rank_tuple(item):
    recovery = item.get("median_recovery_seconds")
    recovery_score = -float(recovery) if recovery is not None else -1e12
    return (
        float(item.get("success_rate") or 0.0),
        int(item.get("observation_count") or 0),
        QUALITY_SCORE.get(item.get("evidence_quality"), 0),
        recovery_score,
    )


def best_playbook(conn, cause, category, min_observations):
    all_items = list_playbooks(
        conn,
        min_observations,
        limit=200,
        cause=cause,
        category=category,
        learned_only=False,
    )
    learned = [item for item in all_items if item["learned"]]
    if not learned:
        sample_count = max(
            [item["observation_count"] for item in all_items],
            default=0,
        )

        return {
            "learned": False,
            "sample_count": sample_count,
            "minimum_observations": min_observations,
            "reason": (
                f"Need at least {min_observations} resolved evidence-backed "
                f"observations; currently have {sample_count}."
            ),
            "causation_note": CAUSATION_NOTE,
        }

    learned.sort(key=_rank_tuple, reverse=True)
    top = dict(learned[0])
    alternatives = [
        {
            "playbook_key": item["playbook_key"],
            "sample_count": item["observation_count"],
            "success_rate": item["success_rate"],
            "success_rate_pct": item["success_rate_pct"],
            "evidence_quality": item["evidence_quality"],
            "median_recovery_seconds": item["median_recovery_seconds"],
        }
        for item in learned[1:4]
    ]
    top["sample_count"] = top["observation_count"]
    top["ranking_reason"] = (
        f"Ranked first among {len(learned)} learned local playbook(s): "
        f"{top['verified_stable_count']}/{top['observation_count']} stable "
        f"({top['success_rate_pct']}%), {top['recurrence_count']} recurrence(s), "
        f"evidence quality {top['evidence_quality']}."
    )

    top["alternatives"] = alternatives
    return top


def learning_summary(conn, min_observations, limit=5):
    items = list_playbooks(
        conn,
        min_observations,
        limit=200,
        learned_only=False,
    )
    learned = [item for item in items if item["learned"]]
    learned.sort(key=_rank_tuple, reverse=True)
    return {
        "minimum_observations": min_observations,
        "observation_count": sum(item["observation_count"] for item in items),
        "playbook_count": len(items),
        "learned_playbook_count": len(learned),
        "top_learned": learned[: max(1, min(20, int(limit)))],
        "causation_note": CAUSATION_NOTE,
        "automatic_actions": "none",
    }
