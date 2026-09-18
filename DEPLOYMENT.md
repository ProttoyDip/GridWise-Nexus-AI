# Deploy GridWise

The deployment contains the FastAPI backend. There is no frontend in this repository.

## Render

1. Push the current implementation and deployment files to GitHub. Many implementation files are currently uncommitted; include the application modules, not just the Dockerfile.
2. In Render, create a Blueprint from this repository using the root `render.yaml`.
3. Set `LLM_PROVIDERS` to your provider name, and `LLM_API_KEY` to its key in Render's secret environment settings. For multiple providers, use `LLM_API_KEY_<PROVIDER>` for each provider instead, as documented in `backend/.env.example`. Set the appropriate `LLM_MODELS_<PROVIDER>` for the production model selection.
4. Deploy, then check `/health`, `/llm/status`, and `/system/status`. Ensure a provider is configured before testing operator notes.
5. Run the judge simulator against the deployed URL to verify the full API:

```powershell
cd backend
.\.venv\Scripts\python.exe judge_simulator.py --base-url https://YOUR-SERVICE.onrender.com --random-cases 24 --output reports/deployed_judge_score.json
```

The Blueprint explicitly selects the free plan. Review Render's current free-service limitations before using it for production. Configure `CORS_ALLOW_ORIGINS` with your frontend's origin when connecting a browser application.

## Docker

From the repository root:

```powershell
docker build -t gridwise-api ./backend
docker run --rm -p 8000:8000 --env-file backend/.env gridwise-api
```

Open `http://localhost:8000/docs`. `PORT` defaults to 8000; hosts can override it. The image uses Python 3.12, runs as an unprivileged user, and checks that CBC can solve a small optimization problem during the build. Only application code and runtime dependency definitions enter the image; local credentials, tests, reports, and the virtual environment are excluded.

One worker preserves the usefulness of in-memory caches. Each additional process or replica has separate caches. Allow sufficient proxy request time for provider fallbacks, which can exceed a minute with long model chains.

## Verification status

Hosting deployment and Linux container verification must be completed on a connected host. Local application tests do not establish that a public endpoint is live.
