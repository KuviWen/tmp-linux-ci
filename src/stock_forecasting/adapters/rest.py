from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from stock_forecasting.application import Application
from stock_forecasting.authorization import (
    IdentityVerificationError,
    PolicyDeniedOutcome,
    SecurityContext,
)
from stock_forecasting.contracts import PredictionPayload
from stock_forecasting.platform.state_store import ImmutableStateConflict

OPENAPI_SOURCE = Path(__file__).parents[3] / "openapi" / "openapi.yaml"

P1_PHASE_BOUNDARIES = {
    modality: {
        "status": "unavailable",
        "reason": "phase_1_optional_modality_out_of_scope",
    }
    for modality in ("documents", "fundamentals", "macro")
}


class SourceCredentialWriteRequest(BaseModel):
    credential_fields: dict[str, str]
    expires_at: str | None = None


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


def _page(title: str, body: str, *, head_extra: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {head_extra}
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
    browser_sessions: dict[str, tuple[SecurityContext, str, float]] = {}
    browser_session_ttl_seconds = 300

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

    @app.exception_handler(RequestValidationError)
    async def redacted_request_validation(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        trace_id = request.headers.get("X-Trace-Id", f"trace-{uuid4()}")
        return JSONResponse(
            {
                "type": "https://example.invalid/problems/request-validation-failed",
                "title": "請求格式無效",
                "status": 422,
                "detail": "The request body does not match the required contract.",
                "instance": request.url.path,
                "trace_id": trace_id,
                "code": "request_validation_failed",
            },
            status_code=422,
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
        if authorization is not None:
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
        session_id = request.cookies.get("stock_forecasting_operations_session")
        session = browser_sessions.get(session_id or "")
        if session is None or session[2] <= time.monotonic():
            if session_id is not None:
                browser_sessions.pop(session_id, None)
            raise HTTPException(status_code=401, detail="authentication_required")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf_token = request.headers.get("X-CSRF-Token")
            if csrf_token is None or not hmac.compare_digest(csrf_token, session[1]):
                raise HTTPException(status_code=403, detail="csrf_validation_failed")
        return session[0]

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

    @app.get("/api/v1/operations/source-credentials", response_model=None)
    def list_source_credentials(
        request: Request,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        outcome = application.operations_control.list_source_credentials(
            trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
            security_context=security_context,
        )
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return {"items": outcome}

    @app.put("/api/v1/operations/source-credentials/{provider_id}", response_model=None)
    def set_source_credential(
        request: Request,
        provider_id: str,
        body: SourceCredentialWriteRequest,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        try:
            outcome = application.operations_control.set_source_credential(
                provider_id=provider_id,
                credential_fields=body.credential_fields,
                expires_at=body.expires_at,
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="source_provider_not_found") from error
        except (ImmutableStateConflict, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return outcome

    @app.post(
        "/api/v1/operations/source-credentials/{provider_id}/rotations",
        response_model=None,
    )
    def rotate_source_credential(
        request: Request,
        provider_id: str,
        body: SourceCredentialWriteRequest,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        try:
            outcome = application.operations_control.rotate_source_credential(
                provider_id=provider_id,
                credential_fields=body.credential_fields,
                expires_at=body.expires_at,
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="source_provider_not_found") from error
        except (ImmutableStateConflict, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return outcome

    @app.delete("/api/v1/operations/source-credentials/{provider_id}", response_model=None)
    def revoke_source_credential(
        request: Request,
        provider_id: str,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        try:
            outcome = application.operations_control.revoke_source_credential(
                provider_id=provider_id,
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="source_provider_not_found") from error
        except (ImmutableStateConflict, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return outcome

    @app.post(
        "/api/v1/operations/source-credentials/{provider_id}/validations",
        response_model=None,
    )
    def validate_source_credential(
        request: Request,
        provider_id: str,
        security_context: SecurityContext = research_authentication,
    ) -> dict[str, object] | Response:
        try:
            outcome = application.operations_control.validate_source_credential(
                provider_id=provider_id,
                trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
                security_context=security_context,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="source_provider_not_found") from error
        except (ImmutableStateConflict, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        return outcome

    @app.get(
        "/operations/source-credentials",
        response_class=HTMLResponse,
        response_model=None,
    )
    def source_credentials_page(
        request: Request,
        security_context: SecurityContext = research_authentication,
    ) -> Response:
        outcome = application.operations_control.list_source_credentials(
            trace_id=request.headers.get("X-Trace-Id", f"trace-{uuid4()}"),
            security_context=security_context,
        )
        if isinstance(outcome, PolicyDeniedOutcome):
            return authorization_denied(request, outcome)
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        browser_sessions[session_id] = (
            security_context,
            csrf_token,
            time.monotonic() + browser_session_ttl_seconds,
        )
        provider_sections: list[str] = []
        for provider in outcome:
            provider_id = escape(str(provider["provider_id"]), quote=True)
            base_endpoint = f"/api/v1/operations/source-credentials/{provider_id}"
            provider_sections.append(
                '<section class="panel" data-provider-id="'
                f'{provider_id}"><h2>{escape(str(provider["display_name"]))}</h2>'
                "<dl><dt>Credential kind</dt><dd>"
                f"{escape(str(provider['credential_kind']))}</dd>"
                '<dt>Readiness</dt><dd class="status">'
                f"{escape(str(provider['readiness']))}</dd>"
                f"<dt>Reason</dt><dd>{escape(str(provider['reason_code']))}</dd>"
                f"<dt>Version</dt><dd>{escape(str(provider['version']))}</dd></dl>"
                '<p><a rel="noreferrer" href="'
                f'{escape(str(provider["registration_url"]), quote=True)}">重新申請帳號</a> '
                '<a rel="noreferrer" href="'
                f'{escape(str(provider["key_management_url"]), quote=True)}">'
                "管理／重新發行 key</a></p>"
                f'<form data-endpoint="{base_endpoint}">'
                '<label>API key ID <input type="password" name="api_key_id" '
                'autocomplete="new-password" required></label>'
                '<label>API secret key <input type="password" name="api_secret_key" '
                'autocomplete="new-password" required></label>'
                '<label>到期時間（選填） <input type="datetime-local" name="expires_at"></label>'
                '<p><button type="button" data-operation="set">儲存</button> '
                '<button type="button" data-operation="rotate">輪替</button> '
                '<button type="button" data-operation="validate">驗證</button> '
                '<button type="button" data-operation="revoke">撤銷</button></p>'
                '<output aria-live="polite"></output></form></section>'
            )
        body = (
            "<main><header><h1>來源憑證管理</h1>"
            "<p>API key、secret 與 token 是 write-only；頁面只顯示 readiness 與版本。</p>"
            "<p>程式不會自動建立帳號、處理 CAPTCHA、email、MFA 或接受條款；"
            "請使用提供者自己的連結完成重新申請。</p></header>"
            + "".join(provider_sections)
            + """<script>
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
for (const button of document.querySelectorAll('[data-operation]')) {
  button.addEventListener('click', async () => {
    const form = button.closest('form');
    const operation = button.dataset.operation;
    const base = form.dataset.endpoint;
    const fields = Object.fromEntries(new FormData(form));
    const expiresAt = fields.expires_at ? new Date(fields.expires_at).toISOString() : null;
    delete fields.expires_at;
    const request = operation === 'set'
      ? {url: base, method: 'PUT', body: {credential_fields: fields, expires_at: expiresAt}}
      : operation === 'rotate'
        ? {
            url: `${base}/rotations`,
            method: 'POST',
            body: {credential_fields: fields, expires_at: expiresAt},
          }
        : operation === 'validate'
          ? {url: `${base}/validations`, method: 'POST'}
          : {url: base, method: 'DELETE'};
    const response = await fetch(request.url, {
      method: request.method,
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
      body: request.body ? JSON.stringify(request.body) : undefined,
    });
    const result = await response.json();
    const credential = result.credential ?? result;
    form.querySelector('output').textContent = response.ok
      ? `${credential.readiness}: ${credential.reason_code} (v${credential.version})`
      : `${result.code || 'request_failed'}`;
    form.reset();
  });
}
</script></main>"""
        )
        response = HTMLResponse(
            _page(
                "來源憑證管理",
                body,
                head_extra=(f'<meta name="csrf-token" content="{escape(csrf_token, quote=True)}">'),
            )
        )
        response.set_cookie(
            "stock_forecasting_operations_session",
            session_id,
            httponly=True,
            max_age=browser_session_ttl_seconds,
            path="/api/v1/operations/source-credentials",
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

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
        market = str(outcome["market"])
        is_us_market = market in {"XNAS", "XNYS"}
        page_title = "美股行情研究資格" if is_us_market else "台股行情研究資格"
        if status == "quarantined":
            state_text = "資料隔離"
            provider_text = "不具研究資格；來源原始證據已隔離保存"
        elif status == "deferred":
            state_text = "來源延後"
            provider_text = "來源限流，尚未取得資料；checkpoint 未前進"
        elif status == "credential_required":
            state_text = "憑證待設定"
            provider_text = "未接觸來源；請由來源憑證管理頁設定並驗證憑證"
        elif status == "policy_blocked":
            current_policy_reasons = {
                str(decision["reason_code"])
                for source in sources
                if isinstance((decision := source.get("current_policy_decision")), dict)
                and decision.get("outcome") == "denied"
            }
            source_was_deferred = any(source["status"] == "deferred" for source in sources)
            source_contacted = outcome["reason_code"] == "source_rights_not_effective" or any(
                source["status"] != "policy_blocked" for source in sources
            )
            if current_policy_reasons and source_was_deferred:
                state_text = "政策阻擋"
                rights_text = (
                    "來源權利已撤銷"
                    if "source_entitlement_revoked" in current_policy_reasons
                    else "來源權利已失效"
                )
                provider_text = f"{rights_text}；先前來源限流，checkpoint 未前進"
            else:
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
        source_basis = cast(dict[str, object], outcome["source_basis"])
        downstream_readiness = cast(dict[str, object], outcome["downstream_readiness"])
        downstream_text = "、".join(
            f"{name}={escape(str(readiness))}" for name, readiness in downstream_readiness.items()
        )
        if source_basis.get("basis_type") == "zero_fee_plan":
            provider_name = (
                "Alpaca Market Data Basic"
                if source_basis.get("provider_id") == "alpaca-market-data-basic"
                else str(source_basis.get("provider_id", "未知 provider"))
            )
            basis_details = (
                f"<dt>零付費認證來源</dt><dd>{escape(provider_name)}</dd>"
                f"<dt>方案</dt><dd>{escape(str(source_basis['plan_id']))}</dd>"
                "<dt>成本邊界</dt><dd>$0 自助帳號；憑證 readiness 不代表用途資格</dd>"
            )
        else:
            basis_details = (
                "<dt>官方公開資料使用依據</dt><dd>"
                f"{escape(str(outcome['source_basis_id']))}</dd>"
                "<dt>成本邊界</dt><dd>免帳號、免申請、免付費；須保留 OGDL 顯名</dd>"
            )
        body = (
            f"<main><header><h1>{page_title}</h1>"
            f'<p class="badge">{state_text}</p>'
            f"<p>{provider_text}</p></header>"
            '<section class="panel"><h2>資格依賴</h2><dl>'
            f"{basis_details}"
            f"<dt>原因</dt><dd>{escape(str(outcome['reason_code']))}</dd>"
            f"<dt>下游一致阻擋</dt><dd>{downstream_text}</dd>"
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
        return _page(page_title, body)

    return app
