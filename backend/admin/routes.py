"""
Admin panel routes — profile, sources, keywords, regions, blocklist,
email settings, applications (with per-application replay), review queue,
and the live event feed.

Auth: password + TOTP 2FA via signed session cookies (backend/auth.py).
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend import auth
from backend.database import get_repo
from backend.services.config_service import ConfigService
from backend.services.orchestrator import orchestrator

router = APIRouter(prefix="/admin", tags=["admin"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

config_service = ConfigService()


def _render(request: Request, template: str, **context):
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={"request": request, "admin_logged_in": auth.admin_logged_in(request), **context},
    )


# =========================================================================
# AUTH
# =========================================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    return _render(request, "login.html", error=error)


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if not auth.verify_password(password):
        return _render(request, "login.html", error="Incorrect password")

    totp_required = auth.totp_configured()
    token = auth.create_session(totp_verified=not totp_required)
    response = RedirectResponse("/admin", status_code=303)
    auth.set_session_cookie(response, token)
    return response


@router.get("/2fa", response_class=HTMLResponse)
async def two_fa_page(request: Request, error: Optional[str] = None):
    if not auth.totp_configured():
        return RedirectResponse("/admin", status_code=303)
    return _render(request, "setup_2fa.html",
                   provisioning_uri=auth.totp_provisioning_uri(), error=error)


@router.post("/2fa")
async def two_fa_submit(request: Request, code: str = Form(...)):
    if not auth.verify_totp(code.strip()):
        return _render(request, "setup_2fa.html",
                       provisioning_uri=auth.totp_provisioning_uri(),
                       error="Invalid code")
    token = auth.create_session(totp_verified=True)
    response = RedirectResponse("/admin", status_code=303)
    auth.set_session_cookie(response, token)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    auth.clear_session_cookie(response)
    return response


# =========================================================================
# PAGES
# =========================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session=Depends(auth.require_admin)):
    repo = get_repo()
    try:
        job_stats = await repo.get_job_counts()
        async with repo.pool.acquire() as conn:
            app_count = await conn.fetchval("SELECT COUNT(*) FROM applications")
            email_count = await conn.fetchval("SELECT COUNT(*) FROM emails WHERE sent_at IS NOT NULL")
            pending = await conn.fetchval(
                "SELECT COUNT(*) FROM pending_confirmations WHERE status = 'pending'"
            )
    except Exception:
        job_stats = {"total_jobs": 0, "jobs_by_region": {}, "jobs_by_status": {}}
        app_count = email_count = pending = 0

    events = await repo.get_events(limit=15)

    return _render(
        request, "dashboard.html",
        job_stats=job_stats,
        total_jobs=job_stats.get("total_jobs", 0),
        total_applications=app_count or 0,
        total_emails=email_count or 0,
        pending_confirmations=pending or 0,
        events=events,
        worker_running=orchestrator.is_worker_running(),
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, session=Depends(auth.require_admin)):
    profile = await config_service.get_profile()
    return _render(request, "profile.html", profile=profile)


@router.get("/profile-answers", response_class=HTMLResponse)
async def profile_answers_page(request: Request, session=Depends(auth.require_admin)):
    repo = get_repo()
    answers = await repo.get_all_answers()
    return _render(request, "profile_answers.html", answers=answers)


@router.get("/keywords", response_class=HTMLResponse)
async def keywords_page(request: Request, session=Depends(auth.require_admin)):
    keywords = await config_service.get_keywords()
    return _render(request, "keywords.html", keywords=keywords)


@router.get("/regions", response_class=HTMLResponse)
async def regions_page(request: Request, session=Depends(auth.require_admin)):
    regions = await config_service.get_regions()
    return _render(request, "regions.html", regions=regions)


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, session=Depends(auth.require_admin)):
    repo = get_repo()
    sources = await repo.get_all_sources()
    sources_config = await config_service.get_sources_config()
    return _render(request, "sources.html", sources=sources, sources_config=sources_config)


@router.get("/blocklist", response_class=HTMLResponse)
async def blocklist_page(request: Request, session=Depends(auth.require_admin)):
    blocklist = await config_service.get_blocklist()
    return _render(request, "blocklist.html", blocklist=blocklist)


@router.get("/email-config", response_class=HTMLResponse)
async def email_config_page(request: Request, session=Depends(auth.require_admin)):
    email_cfg = await config_service.get_email_config()
    limits = await config_service.get_limits()
    return _render(request, "email_config.html", email_cfg=email_cfg, limits=limits)


@router.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, status: Optional[str] = None,
                           session=Depends(auth.require_admin)):
    repo = get_repo()
    applications = await repo.get_applications(status=status, limit=200)
    # Attach job titles
    rows = []
    for app in applications:
        job = await repo.get_job(str(app.job_id)) if app.job_id else None
        rows.append({"app": app, "job": job})
    return _render(request, "applications.html", rows=rows, status_filter=status)


@router.get("/applications/{application_id}", response_class=HTMLResponse)
async def application_detail(request: Request, application_id: str,
                            session=Depends(auth.require_admin)):
    repo = get_repo()
    app = await repo.get_application(application_id)
    if app is None:
        return RedirectResponse("/admin/applications", status_code=303)
    job = await repo.get_job(str(app.job_id)) if app.job_id else None
    timeline = await repo.get_application_timeline(application_id)
    return _render(request, "application_detail.html", app=app, job=job, timeline=timeline)


@router.get("/review", response_class=HTMLResponse)
async def review_page(request: Request, session=Depends(auth.require_admin)):
    repo = get_repo()
    confirmations = await repo.get_pending_confirmations(limit=50)
    rows = []
    for conf in confirmations:
        app = await repo.get_application(str(conf.application_id)) if conf.application_id else None
        job = await repo.get_job(str(app.job_id)) if app and app.job_id else None
        rows.append({"conf": conf, "job": job})
    return _render(request, "review.html", rows=rows)


@router.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, limit: int = 200, session=Depends(auth.require_admin)):
    repo = get_repo()
    events = await repo.get_events(limit=limit)
    return _render(request, "events.html", events=events)


# =========================================================================
# BOT CONTROL
# =========================================================================

@router.post("/start-worker")
async def start_worker(session=Depends(auth.require_admin)):
    started = orchestrator.start_worker()
    return RedirectResponse("/admin", status_code=303)


@router.post("/stop-worker")
async def stop_worker(session=Depends(auth.require_admin)):
    orchestrator.stop_worker()
    return RedirectResponse("/admin", status_code=303)


# =========================================================================
# ACTIONS
# =========================================================================

@router.post("/profile")
async def update_profile(
    name: str = Form(""), email: str = Form(""), university: str = Form(""),
    major: str = Form(""), portfolio_url: str = Form(""),
    skills: str = Form(""),
    session=Depends(auth.require_admin),
):
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    await config_service.update_profile(
        name=name, email=email, university=university, major=major,
        portfolio_url=portfolio_url, skills=skill_list,
    )
    return RedirectResponse("/admin/profile", status_code=303)


@router.post("/profile-answers/add")
async def add_profile_answer(
    question: str = Form(...), answer: str = Form(...), category: str = Form("B"),
    session=Depends(auth.require_admin),
):
    repo = get_repo()
    await repo.save_answer(question, answer, category=category)
    return RedirectResponse("/admin/profile-answers", status_code=303)


@router.post("/profile-answers/{answer_id}/delete")
async def delete_profile_answer(answer_id: str, session=Depends(auth.require_admin)):
    repo = get_repo()
    async with repo.pool.acquire() as conn:
        await conn.execute("DELETE FROM profile_answers WHERE id = $1", answer_id)
    return RedirectResponse("/admin/profile-answers", status_code=303)


@router.post("/keywords")
async def update_keywords(keywords: str = Form(""), session=Depends(auth.require_admin)):
    keyword_list = [k.strip() for k in keywords.splitlines() if k.strip()]
    await config_service.update_keywords(keyword_list)
    return RedirectResponse("/admin/keywords", status_code=303)


@router.post("/regions")
async def update_regions(regions: str = Form(""), session=Depends(auth.require_admin)):
    region_list = [r.strip() for r in regions.splitlines() if r.strip()]
    await config_service.update_regions(region_list)
    return RedirectResponse("/admin/regions", status_code=303)


@router.post("/sources/{source_name}/toggle")
async def toggle_source(source_name: str, session=Depends(auth.require_admin)):
    repo = get_repo()
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "UPDATE sources SET enabled = NOT enabled WHERE name = $1", source_name
        )
    cfg = await config_service.get_sources_config()
    cfg[source_name] = not cfg.get(source_name, True)
    await config_service.update_sources_config(**cfg)
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/blocklist")
async def update_blocklist(
    companies: str = Form(""), domains: str = Form(""),
    session=Depends(auth.require_admin),
):
    company_list = [c.strip() for c in companies.splitlines() if c.strip()]
    domain_list = [d.strip() for d in domains.splitlines() if d.strip()]
    await config_service.update_blocklist(companies=company_list, domains=domain_list)
    return RedirectResponse("/admin/blocklist", status_code=303)


@router.post("/email-config")
async def update_email_config(
    daily_cap: int = Form(50), per_domain_cap: int = Form(3),
    warmup_day: int = Form(1), warmup_increment: int = Form(5),
    kill_switch_bounce_threshold: int = Form(15),
    allow_domain_guess: str = Form(""),
    session=Depends(auth.require_admin),
):
    await config_service.update_email_config(
        daily_cap=daily_cap,
        per_domain_cap=per_domain_cap,
        warmup_day=warmup_day,
        warmup_increment=warmup_increment,
        kill_switch_bounce_threshold=kill_switch_bounce_threshold,
        allow_domain_guess=allow_domain_guess.lower() == "on",
    )
    return RedirectResponse("/admin/email-config", status_code=303)


@router.post("/review/{confirmation_id}/answer")
async def review_answer(confirmation_id: str, answer: str = Form(""),
                        session=Depends(auth.require_admin)):
    repo = get_repo()
    confirmation = await repo.get_confirmation(confirmation_id)
    if confirmation is None:
        return RedirectResponse("/admin/review", status_code=303)

    await repo.answer_confirmation(confirmation_id, answer)

    # Restart-and-refill: requeue the paused application
    if confirmation.application_id:
        app = await repo.get_application(str(confirmation.application_id))
        if app and app.job_id:
            await orchestrator.enqueue_apply(str(app.job_id))

    return RedirectResponse("/admin/review", status_code=303)