"""FastAPI Dashboard app para o Athena-MCP."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from athena import __version__
from athena.combos import (
    ComboStep,
    create_combo,
    delete_combo,
    ensure_default_combo,
    list_combos,
)
from athena.config import DASHBOARD_HOST, DASHBOARD_PORT
from athena.models import ensure_models_fresh
from athena.providers import list_providers
from athena.router import test_combo

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "css").mkdir(exist_ok=True)
(STATIC_DIR / "js").mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Athena-MCP Dashboard", version=__version__)

# CORS — permite frontend em localhost:7100
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:7100", "http://127.0.0.1:7100"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# === PÁGINAS ===

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": __version__})


@app.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request):
    return templates.TemplateResponse(request, "providers.html", {"version": __version__})


@app.get("/combos", response_class=HTMLResponse)
async def combos_page(request: Request):
    return templates.TemplateResponse(request, "combos.html", {"version": __version__})


@app.get("/combos/new", response_class=HTMLResponse)
async def combo_new_page(request: Request):
    providers = list_providers()
    return templates.TemplateResponse(request, "combo_form.html", {
        "version": __version__,
        "providers": providers,
        "combo": None,
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return templates.TemplateResponse(request, "logs.html", {"version": __version__})


# === HTMX PARTIALS ===

@app.get("/hx/providers")
async def hx_providers(request: Request):
    ensure_models_fresh()
    providers = list_providers()
    return templates.TemplateResponse(request, "_providers_list.html", {"providers": providers})


@app.post("/hx/providers/refresh")
async def hx_providers_refresh(request: Request):
    from athena.models import refresh_model_catalog
    refresh_model_catalog(force=True)
    providers = list_providers()
    return templates.TemplateResponse(request, "_providers_list.html", {"providers": providers})


@app.get("/hx/models/{provider_id}")
async def hx_models(request: Request, provider_id: str):
    from athena.models import legacy_catalog_for_provider
    models = legacy_catalog_for_provider(provider_id)
    return templates.TemplateResponse(request, "_models_list.html", {
        "provider_id": provider_id,
        "models": models,
    })


@app.get("/hx/combos")
async def hx_combos(request: Request):
    ensure_default_combo()
    combos = list_combos()
    return templates.TemplateResponse(request, "_combos_list.html", {"combos": combos})


@app.post("/hx/combos")
async def hx_combo_create(
    request: Request,
    combo_id: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    provider_1: str = Form(...),
    model_1: str = Form(""),
    provider_2: str = Form(""),
    model_2: str = Form(""),
    provider_3: str = Form(""),
    model_3: str = Form(""),
):
    chain = [ComboStep(provider_id=provider_1, model=model_1 or None)]
    if provider_2:
        chain.append(ComboStep(provider_id=provider_2, model=model_2 or None))
    if provider_3:
        chain.append(ComboStep(provider_id=provider_3, model=model_3 or None))

    create_combo(combo_id, name, chain, description=description)
    combos = list_combos()
    return templates.TemplateResponse(request, "_combos_list.html", {"combos": combos})


@app.delete("/hx/combos/{combo_id}")
async def hx_combo_delete(request: Request, combo_id: str):
    delete_combo(combo_id)
    combos = list_combos()
    return templates.TemplateResponse(request, "_combos_list.html", {"combos": combos})


@app.post("/hx/combos/{combo_id}/test")
async def hx_combo_test(request: Request, combo_id: str):
    results = test_combo(combo_id)
    return templates.TemplateResponse(request, "_combo_test_results.html", {
        "combo_id": combo_id,
        "results": results,
    })


@app.get("/hx/usage")
async def hx_usage(request: Request):
    from athena.usage import get_usage
    usage = get_usage()
    total_calls = sum(u.get("calls", 0) for u in usage.values())
    return templates.TemplateResponse(request, "_usage_card.html", {
        "usage": usage,
        "total_calls": total_calls,
    })


@app.get("/hx/status")
async def hx_status():
    return HTMLResponse("🟢 Online")


# === API JSON ===

@app.get("/api/v1/providers")
async def api_providers():
    ensure_models_fresh()
    return {"providers": list_providers()}


@app.get("/api/v1/combos")
async def api_combos():
    ensure_default_combo()
    return {"combos": [c.to_dict() for c in list_combos()]}


@app.get("/api/v1/usage")
async def api_usage():
    from athena.usage import get_usage
    return {"usage": get_usage()}


@app.get("/api/v1/ratings")
async def api_ratings():
    from athena.ratings import get_ratings_payload
    installed = []
    for p in list_providers():
        if not p.get("available"):
            continue
        for m in p.get("models", []):
            installed.extend([m.get("id", ""), m.get("name", "")])
    return get_ratings_payload(installed or None)


def run_dashboard():
    import uvicorn
    ensure_default_combo()
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT)
