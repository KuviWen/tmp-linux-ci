from datetime import date, timedelta

from stock_forecasting.forecasting import FeatureBatch, FeatureRow, TrendLabel


def engineering_model_history() -> FeatureBatch:
    rows: list[FeatureRow] = []
    labels: tuple[TrendLabel, ...] = ("up", "flat", "down")
    values = {"up": (2.0, 0.0), "flat": (0.0, 2.0), "down": (-2.0, -2.0)}
    for year in (2023, 2024, 2025):
        for quarter in range(1, 5):
            month = (quarter - 1) * 3 + 1
            cursor = date(year, month, 1)
            sessions: list[date] = []
            while len(sessions) < 21:
                if cursor.weekday() < 5:
                    sessions.append(cursor)
                cursor += timedelta(days=1)
            for session_index, session_date in enumerate(sessions):
                label = labels[session_index % len(labels)]
                for market in ("XTAI", "XNAS"):
                    for horizon in (1, 5, 20):
                        rows.append(
                            FeatureRow(
                                row_id=(f"{market}-{horizon}-{session_date.isoformat()}"),
                                market=market,
                                horizon_sessions=horizon,
                                values=values[label],
                                label=label,
                                session_date=session_date,
                            )
                        )
    return FeatureBatch(
        feature_batch_id="feature-batch-engineering-12q",
        source_policy_manifest_id="source-policy-engineering-v1",
        label_manifest_id="label-v1",
        fold_manifest_id="fold-plan-v1",
        cost_manifest_id="cost-zero-fee-v1",
        rows=tuple(rows),
    )
