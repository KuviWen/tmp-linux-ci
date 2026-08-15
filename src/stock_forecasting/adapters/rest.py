from __future__ import annotations

import hashlib
import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from stock_forecasting.application import Application
from stock_forecasting.authorization import (
    IdentityVerificationError,
    PolicyDeniedOutcome,
    SecurityContext,
)
from stock_forecasting.contracts import PredictionPayload

OPENAPI_SOURCE = Path(__file__).parents[3] / "openapi" / "openapi.yaml"

P1_PHASE_BOUNDARIES = {
    modality: {
        "status": "unavailable",
        "reason": "phase_1_optional_modality_out_of_scope",
    }
    for modality in ("documents", "fundamentals", "macro")
}


def _etag(payload: object) -> str:
    encoded = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f'"{hashlib.sha256(encoded).hexdigest()}"'


def _parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; background: #f4f7fb; color: #172033; }}
    main {{ max-width: 1100px; margin: auto; padding: 2rem 1rem; }}
    .panel {{
      background: white; border: 1px solid #cbd5e1; border-radius: .75rem;
      padding: 1rem; margin: 1rem 0;
    }}
    .badge {{
      display: inline-block; border: 2px solid #9a3412; color: #9a3412;
      border-radius: 999px; padding: .2rem .6rem; font-weight: 700;
    }}
    .horizons {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
      gap: .75rem;
    }}
    .horizon {{ border: 1px solid #94a3b8; border-radius: .5rem; padding: .75rem; }}
    .status {{ font-weight: 700; }}
    nav a {{ margin-right: .75rem; }}
    a:focus-visible {{ outline: 3px solid #2563eb; outline-offset: 3px; }}
    dl {{ display: grid; grid-template-columns: minmax(10rem, 14rem) 1fr; gap: .4rem 1rem; }}
    dt {{ font-weight: 700; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
  </style>
</head>
<body>{body}</body>
</html>"""


def _horizon_cards(
    predictions: list[PredictionPayload],
    *,
    focused_horizon: int | None = None,
) -> str:
    cards: list[str] = []
    ordered = sorted(
        predictions,
        key=lambda prediction: prediction["horizon_sessions"] != focused_horizon,
    )
    for prediction in ordered:
        horizon = prediction["horizon_sessions"]
        focused = ' data-focused="true"' if horizon == focused_horizon else ""
        if prediction["prediction_status"] == "unavailable":
            reason = prediction["unavailable_reason"]["code"]
            cards.append(
                f'<section class="horizon"{focused}><h3>{horizon} 個交易日後</h3>'
                f'<p class="status">不可預測：{escape(str(reason))}</p></section>'
            )
            continue
        probabilities = prediction["probabilities"]
        cards.append(
            f'<section class="horizon"{focused}><h3>{horizon} 個交易日後</h3>'
            f"<p>上漲 {probabilities['up'] * 100:.1f}%</p>"
            f"<p>盤整 {probabilities['flat'] * 100:.1f}%</p>"
            f"<p>下跌 {probabilities['down'] * 100:.1f}%</p>"
            f"<p>信心 {prediction['confidence_score'] * 100:.1f}%</p>"
            '<p class="status">資料支援：完整</p></section>'
        )
    return "".join(cards)


def _projection_status(projection: dict[str, object]) -> str:
    state = "等待恢復" if projection["stale"] else "已同步"
    return (
        f'<p class="status">投影狀態：{state}</p>'
        f"<p>核心版本 {projection['core_projection_version']}／"
        f"證據版本 {projection['evidence_projection_version']}</p>"
    )


def create_web_app(application: Application) -> FastAPI:
    app = FastAPI(
        title="台美個股趨勢研究",
        version="1.0.0",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/openapi/openapi.yaml", include_in_schema=False)
    def openapi_source() -> FileResponse:
        return FileResponse(OPENAPI_SOURCE, media_type="application/yaml")

    @app.exception_handler(HTTPException)
    async def problem_details(request: Request, error: HTTPException) -> JSONResponse:
        trace_id = request.headers.get("X-Trace-Id", f"trace-{uuid4()}")
        if error.status_code == 401 and error.detail == "authentication_required":
            payload = {
                "type": "https://example.invalid/problems/authentication-required",
                "title": "Authentication required",
                "status": 401,
                "detail": "A valid local API key is required.",
                "instance": request.url.path,
                "trace_id": trace_id,
                "code": "authentication_required",
            }
        elif error.status_code == 404 and error.detail == "listing_not_found":
            payload = {
                "type": "https://example.invalid/problems/listing-not-found",
                "title": "找不到掛牌研究資源",
                "status": 404,
                "detail": "指定的掛牌在此資訊截止點不存在或不可見。",
                "instance": request.url.path,
                "trace_id": trace_id,
                "code": "listing_not_found",
            }
        else:
            payload = {
                "type": "https://example.invalid/problems/request-failed",
                "title": "請求失敗",
                "status": error.status_code,
                "detail": str(error.detail),
                "instance": request.url.path,
                "trace_id": trace_id,
                "code": "request_failed",
            }
        return JSONResponse(
            payload,
            status_code=error.status_code,
            media_type="application/problem+json",
        )

    def authorization_denied(
        request: Request,
        outcome: PolicyDeniedOutcome,
    ) -> JSONResponse:
        return JSONResponse(
            {
                "type": "https://example.invalid/problems/authorization-denied",
                "title": "Authorization denied",
                "status": 403,
                "detail": "The requested operation is not authorized.",
                "instance": request.url.path,
                "trace_id": outcome.correlation_id,
                "code": outcome.code,
            },
            status_code=403,
            media_type="application/problem+json",
        )

    def authenticate_research_request(
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> SecurityContext:
        if authorization is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        try:
            client_host = request.client.host if request.client is not None else ""
            return application.authenticate_local_request(
                authorization,
                client_host=client_host,
            )
        except IdentityVerificationError as error:
            raise HTTPException(
                status_code=401,
                detail="authentication_required",
            ) from error

    research_authentication = Depends(authenticate_research_request)

    @app.get("/livez")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/startupz")
    def startup() -> dict[str, str]:
        return {"status": "started"}

    @app.get("/readyz")
    def ready() -> dict[str, str]:
        if not application.state_store.ping():
            raise HTTPException(status_code=503, detail="state_store_unavailable")
        return {"status": "ready"}

    @app.get("/api/v1/operations/health")
    def list_health(scope: str = Query(...)) -> dict[str, object]:
        return {"items": application.operations_control.list_health(scope=scope)}

    @app.get("/api/v1/research/predictions", response_model=None)
    def list_predictions(
        request: Request,
        response: Response,
        information_cutoff: str = Query(...),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        trace_id = request.headers.get("X-Trace-Id", f"trace-{uuid4()}")
        query_outcome = application.research_query.list_predictions(
            execution_purpose="fixture",
            trace_id=trace_id,
            security_context=security_context,
        )
        if isinstance(query_outcome, PolicyDeniedOutcome):
            return authorization_denied(request, query_outcome)
        records = [
            record for record in query_outcome if record["information_cutoff"] == information_cutoff
        ]
        items = [
            {
                "listing_id": record["identity"]["listing_id"],
                "display_ticker": record["identity"]["display_ticker"],
                "market": record["calendar"]["exchange"],
                "fixture_badge": record["fixture_badge"],
                "predictions": record["predictions"],
                "lineage": record["lineage"],
                "projection": record["projection"],
            }
            for record in records
        ]
        payload: dict[str, object] = {
            "information_cutoff": information_cutoff,
            "execution_purpose": "fixture",
            "phase_boundaries": P1_PHASE_BOUNDARIES,
            "items": items,
        }
        tag = _etag(payload)
        if if_none_match == tag:
            return Response(status_code=304, headers={"ETag": tag})
        response.headers["ETag"] = tag
        return payload

    @app.get("/api/v1/research/listings/{listing_id}", response_model=None)
    def get_listing_research(
        request: Request,
        listing_id: str,
        information_cutoff: str = Query(...),
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        try:
            query_outcome = application.research_query.get_listing_research(
                listing_id=listing_id,
                information_cutoff=_parse_instant(information_cutoff),
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="listing_not_found") from error
        if isinstance(query_outcome, PolicyDeniedOutcome):
            return authorization_denied(request, query_outcome)
        return query_outcome

    @app.get(
        "/api/v1/research/listings/{listing_id}/price-eligibility",
        response_model=None,
    )
    def get_listing_price_eligibility(
        request: Request,
        listing_id: str,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        try:
            outcome = application.price_eligibility_query.get_listing(
                listing_id=listing_id,
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="listing_not_found") from error
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return outcome

    @app.get("/api/v1/operations/sources", response_model=None)
    def list_price_sources(
        request: Request,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        outcome = application.price_eligibility_query.list_sources(
            trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
            security_context=security_context,
        )
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return {"items": outcome}

    @app.get("/research", response_class=HTMLResponse, response_model=None)
    def research_matrix(
        request: Request,
        information_cutoff: str = Query(...),
        horizon: int = Query(5),
        market: str = Query("all"),
        support: str = Query("full"),
        sort: str = Query("confidence_desc"),
        security_context: SecurityContext = research_authentication,
    ) -> str | Response:
        trace_id = request.headers.get("X-Trace-Id", f"trace-{uuid4()}")
        query_outcome = application.research_query.list_predictions(
            execution_purpose="fixture",
            trace_id=trace_id,
            security_context=security_context,
        )
        if isinstance(query_outcome, PolicyDeniedOutcome):
            return authorization_denied(request, query_outcome)
        records = [
            record
            for record in query_outcome
            if record["information_cutoff"] == information_cutoff
            and (market == "all" or record["calendar"]["exchange"] == market)
        ]
        records = [
            record
            for record in records
            if support == "all"
            or any(
                prediction["horizon_sessions"] == horizon
                and prediction["data_support"]["price_volume"] == support
                for prediction in record["predictions"]
            )
        ]
        if sort == "confidence_desc":
            records.sort(
                key=lambda record: next(
                    (
                        prediction.get("confidence_score", -1.0)
                        for prediction in record["predictions"]
                        if prediction["horizon_sessions"] == horizon
                    ),
                    -1.0,
                ),
                reverse=True,
            )
        rows: list[str] = []
        for record in records:
            listing_id = str(record["identity"]["listing_id"])
            detail_query = urlencode(
                {
                    "information_cutoff": information_cutoff,
                    "horizon": horizon,
                    "market": market,
                    "support": support,
                    "sort": sort,
                    "tab": "forecast",
                }
            )
            rows.append(
                '<article class="panel">'
                f'<p class="badge">{escape(str(record["fixture_badge"]))}</p>'
                f"<h2>{escape(str(record['identity']['display_ticker']))} · "
                f"{escape(str(record['calendar']['exchange']))}</h2>"
                f"<p>資訊截止點 {escape(information_cutoff)}</p>"
                f"{_projection_status(record['projection'])}"
                f'<div class="horizons">'
                f"{_horizon_cards(record['predictions'], focused_horizon=horizon)}</div>"
                f'<p><a href="/research/listings/{escape(listing_id)}?{detail_query}">'
                "開啟標的研究頁</a></p></article>"
            )
        body = (
            "<main><header><p>研究決策支援系統</p><h1>比較矩陣</h1>"
            "<p>所有結果均為 fixture 工程證據，不是正式預測。</p>"
            '<p class="status">文件、基本面、總體模態：P1 尚未提供</p></header>'
            f'<section aria-label="目前檢視條件"><p>期間焦點 {horizon}</p>'
            f"<p>市場 {escape(market)}</p><p>資料支援 {escape(support)}</p>"
            f"<p>排序 {escape(sort)}</p></section>"
            + ("".join(rows) if rows else '<p class="panel">無符合條件的研究結果</p>')
            + "</main>"
        )
        return _page("比較矩陣", body)

    @app.get(
        "/research/listings/{listing_id}",
        response_class=HTMLResponse,
        response_model=None,
    )
    def listing_research_page(
        request: Request,
        listing_id: str,
        information_cutoff: str = Query(...),
        horizon: int = Query(5),
        market: str = Query("XTAI"),
        support: str = Query("full"),
        sort: str = Query("confidence_desc"),
        tab: str = Query("forecast"),
        security_context: SecurityContext = research_authentication,
    ) -> str | Response:
        try:
            query_outcome = application.research_query.get_listing_research(
                listing_id=listing_id,
                information_cutoff=_parse_instant(information_cutoff),
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="listing_not_found") from error
        if isinstance(query_outcome, PolicyDeniedOutcome):
            return authorization_denied(request, query_outcome)
        record = query_outcome

        tab_names = {"forecast": "預測", "lineage": "版本追溯"}
        tab_links: list[str] = []
        for tab_id, label in tab_names.items():
            query = urlencode(
                {
                    "information_cutoff": information_cutoff,
                    "horizon": horizon,
                    "market": market,
                    "support": support,
                    "sort": sort,
                    "tab": tab_id,
                }
            )
            current = ' aria-current="page"' if tab_id == tab else ""
            tab_links.append(
                f'<a href="/research/listings/{escape(listing_id)}?{query}"{current}>{label}</a>'
            )
        lineage = record["lineage"]
        lineage_html = (
            "<dl>"
            f"<dt>FeatureSnapshot</dt><dd>{escape(str(lineage['feature_snapshot_id']))}</dd>"
            f"<dt>ModelArtifact</dt><dd>{escape(str(lineage['model_artifact_id']))}</dd>"
            f"<dt>服務指派</dt><dd>{escape(str(lineage['serving_assignment_id']))}</dd>"
            f"<dt>資料集版本</dt><dd>{escape(str(lineage['dataset_version_id']))}</dd>"
            f"<dt>原始資料物件</dt><dd>{escape(str(lineage['raw_artifact_id']))}</dd>"
            "</dl>"
        )
        horizon_cards = _horizon_cards(record["predictions"], focused_horizon=horizon)
        body = (
            '<main><p><a href="/research?'
            + urlencode(
                {
                    "information_cutoff": information_cutoff,
                    "horizon": horizon,
                    "market": market,
                    "support": support,
                    "sort": sort,
                }
            )
            + '">返回比較矩陣</a></p><header>'
            f'<p class="badge">{escape(str(record["fixture_badge"]))}</p>'
            "<h1>標的研究頁</h1>"
            f"<p>{escape(str(record['identity']['display_ticker']))} · "
            f"{escape(str(record['calendar']['exchange']))}</p>"
            f"<p>資訊截止點 {escape(information_cutoff)}</p></header>"
            f"{_projection_status(record['projection'])}"
            f'<nav aria-label="研究細節">{"".join(tab_links)}</nav>'
            f'<section class="panel"><div class="horizons">{horizon_cards}</div></section>'
            f'<section class="panel"><h2>版本追溯</h2>{lineage_html}</section></main>'
        )
        return _page("標的研究頁", body)

    @app.get(
        "/research/listings/{listing_id}/price-eligibility",
        response_class=HTMLResponse,
        response_model=None,
    )
    def listing_price_eligibility_page(
        request: Request,
        listing_id: str,
        security_context: SecurityContext = research_authentication,
    ) -> str | Response:
        try:
            outcome = application.price_eligibility_query.get_listing(
                listing_id=listing_id,
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="listing_not_found") from error
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        sources = cast(list[dict[str, object]], outcome["sources"])
        status = str(outcome["status"])
        if status == "quarantined":
            state_text = "資料隔離"
            provider_text = "不具研究資格；來源原始證據已隔離保存"
        elif status == "deferred":
            state_text = "來源延後"
            provider_text = "來源限流，尚未取得資料；checkpoint 未前進"
        elif status == "policy_blocked":
            source_contacted = any(source["status"] != "policy_blocked" for source in sources)
            state_text = "資格阻擋" if source_contacted else "政策阻擋"
            provider_text = (
                "來源候選資料已保存；不具正式研究資格" if source_contacted else "未接觸來源"
            )
        else:
            state_text = "已具研究資格"
            provider_text = "來源資料已保存"
        source_rows = "".join(
            "<tr>"
            f"<td>{escape(str(source['source_mode']))}</td>"
            f"<td>{escape(str(source['source_id']))}</td>"
            f"<td>{escape(str(source['status']))}</td>"
            f"<td>{escape(str(source['reason_code']))}</td>"
            "</tr>"
            for source in sources
        )
        dataset_id = next(
            (
                source["dataset_version_id"]
                for source in sources
                if source["dataset_version_id"] is not None
            ),
            None,
        )
        adjustment_id = next(
            (
                source["adjustment_version_id"]
                for source in sources
                if source["adjustment_version_id"] is not None
            ),
            None,
        )
        body = (
            "<main><header><h1>台股行情研究資格</h1>"
            f'<p class="badge">{state_text}</p>'
            f"<p>{provider_text}</p></header>"
            '<section class="panel"><h2>資格依賴</h2><dl>'
            f"<dt>外部依賴</dt><dd>{escape(str(outcome['dependency_id']))}</dd>"
            f"<dt>原因</dt><dd>{escape(str(outcome['reason_code']))}</dd>"
            f"<dt>資料集版本</dt><dd>{escape(str(dataset_id)) if dataset_id else '尚未建立'}</dd>"
            "<dt>調整版本</dt>"
            f"<dd>{escape(str(adjustment_id)) if adjustment_id else '尚未建立'}</dd>"
            "</dl>"
            f"<p>資料集版本：{escape(str(dataset_id)) if dataset_id else '尚未建立'}</p>"
            f"<p>調整版本：{escape(str(adjustment_id)) if adjustment_id else '尚未建立'}</p>"
            "</section>"
            '<section class="panel"><h2>來源狀態</h2><table><thead><tr>'
            "<th>模式</th><th>來源</th><th>狀態</th><th>原因</th>"
            f"</tr></thead><tbody>{source_rows}</tbody></table></section></main>"
        )
        return _page("台股行情研究資格", body)

    return app
