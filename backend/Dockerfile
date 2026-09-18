FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY requirements-runtime.txt ./requirements-runtime.txt
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Fail the image build if the bundled Linux CBC solver cannot run.
RUN python -c "import pulp; p = pulp.LpProblem('cbc_check', pulp.LpMinimize); x = pulp.LpVariable('x', lowBound=1); p += x; p.solve(pulp.PULP_CBC_CMD(msg=False)); assert p.status == pulp.LpStatusOptimal; assert abs(x.value() - 1) < 1e-7"

RUN useradd --create-home --uid 10001 gridwise && chown gridwise:gridwise /app
COPY --chown=gridwise:gridwise app ./app
USER gridwise

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=3)"

CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
