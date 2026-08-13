from __future__ import annotations

import hashlib
import json
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from stock_forecasting.application import Application
from stock_forecasting.authorization import (
    AuthorizationDenied,
    IdentityVerificationError,
    SecurityContext,
)
from stock_forecasting.contracts import PredictionPayload

OPENAPI_SOURCE = Path(__file__).parents[3] / "openapi" / "openapi.yaml"


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

    @app.exception_handler(AuthorizationDenied)
    async def authorization_denied(
        request: Request,
        error: AuthorizationDenied,
    ) -> JSONResponse:
        return JSONResponse(
            {
                "type": "https://example.invalid/problems/authorization-denied",
                "title": "Authorization denied",
                "status": 403,
                "detail": "The requested operation is not authorized.",
                "instance": request.url.path,
                "trace_id": error.correlation_id,
                "code": error.public_code,
            },
            status_code=403,
            media_type="application/problem+json",
        )

    def authenticate_research_request(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> SecurityContext:
        if authorization is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        try:
            return application.authenticate_local_request(authorization)
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
        records = [
            record
            for record in application.research_query.list_predictions(
                execution_purpose="fixture",
                trace_id=trace_id,
                security_context=security_context,
            )
            if record["information_cutoff"] == information_cutoff
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
            "items": items,
        }
        tag = _etag(payload)
        if if_none_match == tag:
            return Response(status_code=304, headers={"ETag": tag})
        response.headers["ETag"] = tag
        return payload

    @app.get("/api/v1/research/listings/{listing_id}")
    def get_listing_research(
        request: Request,
        listing_id: str,
        information_cutoff: str = Query(...),
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object]:
        try:
            return application.research_query.get_listing_research(
                listing_id=listing_id,
                information_cutoff=_parse_instant(information_cutoff),
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="listing_not_found") from error

    @app.get("/research", response_class=HTMLResponse)
    def research_matrix(
        request: Request,
        information_cutoff: str = Query(...),
        horizon: int = Query(5),
        market: str = Query("all"),
        support: str = Query("full"),
        sort: str = Query("confidence_desc"),
        security_context: SecurityContext = research_authentication,
    ) -> str:
        trace_id = request.headers.get("X-Trace-Id", f"trace-{uuid4()}")
        records = [
            record
            for record in application.research_query.list_predictions(
                execution_purpose="fixture",
                trace_id=trace_id,
                security_context=security_context,
            )
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
            "<p>所有結果均為 fixture 工程證據，不是正式預測。</p></header>"
            f'<section aria-label="目前檢視條件"><p>期間焦點 {horizon}</p>'
            f"<p>市場 {escape(market)}</p><p>資料支援 {escape(support)}</p>"
            f"<p>排序 {escape(sort)}</p></section>"
            + ("".join(rows) if rows else '<p class="panel">無符合條件的研究結果</p>')
            + "</main>"
        )
        return _page("比較矩陣", body)

    @app.get("/research/listings/{listing_id}", response_class=HTMLResponse)
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
    ) -> str:
        try:
            record = application.research_query.get_listing_research(
                listing_id=listing_id,
                information_cutoff=_parse_instant(information_cutoff),
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="listing_not_found") from error

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

    return app
