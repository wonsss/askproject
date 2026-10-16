"""웹 콘솔 서버(Starlette). 로컬 전용(127.0.0.1), 읽기 전용, 외부 리소스 없음.

감사 로그에 사내 코드가 들어가므로 127.0.0.1에서도 접근을 좁힌다.
- Host 헤더 검증: DNS rebinding(악성 페이지가 자기 도메인을 127.0.0.1로 돌려 콘솔을 읽는 공격) 차단
- 실행당 세션 토큰: 앱이 `?key=토큰` URL로 열면 HttpOnly·SameSite=Strict 쿠키로 바꾸고 토큰 없는 주소로 보낸다.
  같은 맥의 다른 웹 페이지·프로세스는 토큰을 모른다(data/console.json, 권한 0600).
- 팀 공개(Cloudflare Tunnel + Access, docs/operations.md): 공개 호스트로 온 요청은 토큰 대신 Cloudflare Access가
  붙인 JWT(Cf-Access-Jwt-Assertion)를 팀 공개 키로 검증한다. Host 헤더만으로는 통과시키지 않는다(로컬 우회 방지).
"""

from __future__ import annotations

import hmac
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from askproject.console import views
from askproject.console.data import ConsoleData, compare_runs

CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")
FILTER_KEYS = ("q", "domain", "route", "confidence", "feedback", "days", "source", "ops", "reason")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"  # GitHub 링크로 콘솔 주소를 넘기지 않는다
        response.headers["Cache-Control"] = "no-store"
        return response


COOKIE = "askproject_console"
CF_JWT_HEADER = "cf-access-jwt-assertion"


class CloudflareAccess:
    """Cloudflare Access JWT 검증(RS256, 팀 인증서 https://<팀>.cloudflareaccess.com/cdn-cgi/access/certs)."""

    def __init__(self, team_domain: str, audience: str, jwk_client=None):
        import jwt

        self.issuer = f"https://{team_domain}"
        self.audience = audience
        self._jwks = jwk_client or jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True)

    def verify(self, token: str) -> bool:
        import jwt

        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                       options={"require": ["exp", "iss", "aud"]})  # 만료 없는 토큰은 받지 않는다
            return True
        except Exception:
            return False


class AccessGuard(BaseHTTPMiddleware):
    def __init__(self, app, allowed_hosts: set[str] | None, token: str | None,
                 public_host: str | None = None, cf_access: CloudflareAccess | None = None):
        super().__init__(app)
        self.allowed_hosts = allowed_hosts
        self.token = token
        self.public_host = public_host
        self.cf_access = cf_access

    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        if self.public_host and host == self.public_host:
            jwt_token = request.headers.get(CF_JWT_HEADER, "")
            if self.cf_access and jwt_token and self.cf_access.verify(jwt_token):
                return await call_next(request)
            return PlainTextResponse("Cloudflare Access 인증이 필요해요", status_code=403)
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            return PlainTextResponse("허용되지 않은 호스트", status_code=421)
        if self.token is None:
            return await call_next(request)
        key = request.query_params.get("key")
        if key is not None:
            if not hmac.compare_digest(key, self.token):
                return PlainTextResponse("콘솔 키가 맞지 않아요. 메뉴 막대 앱의 '웹 콘솔' 버튼으로 여세요.", status_code=401)
            rest = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "key"])
            resp = RedirectResponse(request.url.path + (f"?{rest}" if rest else ""), status_code=303)
            resp.set_cookie(COOKIE, self.token, httponly=True, samesite="strict", path="/")
            return resp
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), self.token):
            return PlainTextResponse("메뉴 막대 앱의 '웹 콘솔' 버튼으로 열어 주세요(실행할 때마다 새 키가 발급돼요).",
                                     status_code=401)
        return await call_next(request)


def _params(request: Request) -> dict:
    return {k: request.query_params[k] for k in FILTER_KEYS if request.query_params.get(k)}


def create_app(data: ConsoleData, allowed_hosts: set[str] | None = None, token: str | None = None,
               public_host: str | None = None, cf_access: CloudflareAccess | None = None) -> Starlette:
    names = data.domain_names

    async def home(request: Request):
        recent = [r for r in data.rows("slack") if r.has_answer][:10]
        return HTMLResponse(views.home(data.stats(7), recent, len(data.needs_check()), names))

    async def search(request: Request):
        p = _params(request)
        source = p.get("source") or ("all" if p.get("ops") == "1" else "slack")
        rows = data.search(p.get("q", ""), source=source, domain=p.get("domain", ""), route=p.get("route", ""),
                           confidence=p.get("confidence", ""), feedback=p.get("feedback", ""),
                           days=int(p["days"]) if p.get("days", "").isdigit() else 0)
        return HTMLResponse(views.search_page({**p, "source": source}, rows, names))

    async def question(request: Request):
        row = data.get(request.path_params["qid"])
        if not row:
            return HTMLResponse(views.not_found("질문을 찾을 수 없어요"), status_code=404)
        cites = data.citations(row)
        view = request.query_params.get("view", "")
        return HTMLResponse(views.detail(row, view, cites, data.freshness(row, cites), data.latest_commit(),
                                         data.tool_steps(row.id) if view == "ops" else [],
                                         data.usage(row.id), data.feedback_events(row.id), names))

    async def check(request: Request):
        p = _params(request)
        return HTMLResponse(views.check_page(data.needs_check(p.get("source", "slack")), names, p))

    async def evals(request: Request):
        return HTMLResponse(views.eval_page(data.eval_runs()))

    async def eval_run(request: Request):
        run = data.eval_run(request.path_params["name"])
        if not run:
            return HTMLResponse(views.not_found("평가 회차를 찾을 수 없어요"), status_code=404)
        return HTMLResponse(views.eval_run_page(run))

    async def eval_compare(request: Request):
        a, b = data.eval_run(request.query_params.get("a", "")), data.eval_run(request.query_params.get("b", ""))
        if not (a and b):
            return HTMLResponse(views.not_found("비교할 회차를 찾을 수 없어요"), status_code=404)
        return HTMLResponse(views.compare_page(a, b, compare_runs(a, b)))

    routes = [
        Route("/", home), Route("/search", search), Route("/q/{qid}", question), Route("/check", check),
        Route("/eval", evals), Route("/eval/compare", eval_compare), Route("/eval/{name}", eval_run),
    ]
    guard = Middleware(AccessGuard, allowed_hosts=allowed_hosts, token=token, public_host=public_host, cf_access=cf_access)
    return Starlette(routes=routes, middleware=[guard,
                                                Middleware(SecurityHeaders)])
