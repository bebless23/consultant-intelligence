"""
=============================================================
GLOBAL PROFESSIONAL SERVICES INSIGHTS — REST API
=============================================================
FastAPI backend exposing all database resources.
Powers the client-facing dashboard and external integrations.

Run:
    uvicorn api:app --reload --host 0.0.0.0 --port 8000

Docs available at:
    http://localhost:8000/docs     (Swagger UI)
    http://localhost:8000/redoc    (ReDoc)

Requirements:
    pip install fastapi uvicorn psycopg2-binary python-dotenv pydantic
"""

import os
import logging
from datetime import date, datetime
from typing import Optional
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────
app = FastAPI(
    title="Global Professional Services Insights API",
    description="Intelligence platform for Legal, Medical, and AI sector trends.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.getenv("DB_NAME", "insights_db"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

def get_db():
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


# ─────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────
class SectorOut(BaseModel):
    id: int
    name: str
    description: Optional[str]

class SourceOut(BaseModel):
    id: int
    firm_name: str
    report_title: Optional[str]
    published_date: Optional[date]
    url: Optional[str]
    report_type: Optional[str]
    is_automated: bool

class TrendOut(BaseModel):
    id: int
    sector: str
    name: str
    description: Optional[str]
    direction: str
    status: str
    period_start: Optional[date]
    period_end: Optional[date]
    source_firm: Optional[str]

class IssueOut(BaseModel):
    id: int
    sector: str
    trend: Optional[str]
    title: str
    description: Optional[str]
    severity: Optional[int]
    status: str

class SolutionOut(BaseModel):
    id: int
    issue: str
    title: str
    description: Optional[str]
    solution_type: str
    effectiveness: Optional[int]
    source_firm: str

class InsightOut(BaseModel):
    id: int
    title: str
    summary: str
    sector: str
    trend: Optional[str]
    trend_direction: Optional[str]
    issue: Optional[str]
    issue_severity: Optional[int]
    solution: Optional[str]
    solution_type: Optional[str]
    solution_effectiveness: Optional[int]
    source_firm: str
    source_report: Optional[str]
    report_date: Optional[date]
    status: str
    published_date: Optional[date]

class InsightListOut(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InsightOut]

class SectorSummaryOut(BaseModel):
    sector: str
    total_trends: int
    total_issues: int
    total_solutions: int
    total_insights: int
    avg_issue_severity: Optional[float]

class TagOut(BaseModel):
    id: int
    name: str
    category: Optional[str]

class TrendTrackerOut(BaseModel):
    id: int
    sector: str
    trend: str
    direction: str
    status: str
    period_start: Optional[date]
    open_issues: int
    source_firm: str


# ─────────────────────────────────────────────
# ROUTES — SECTORS
# ─────────────────────────────────────────────
@app.get("/sectors", response_model=list[SectorOut], tags=["Sectors"])
def list_sectors(db=Depends(get_db)):
    """Return all sectors."""
    with db.cursor() as cur:
        cur.execute("SELECT id, name, description FROM sectors ORDER BY name")
        return cur.fetchall()


# ─────────────────────────────────────────────
# ROUTES — INSIGHTS
# ─────────────────────────────────────────────
@app.get("/insights", response_model=InsightListOut, tags=["Insights"])
def list_insights(
    sector:   Optional[str] = Query(None, description="Filter by sector name"),
    status:   Optional[str] = Query("Active", description="Active | Emerging | Declining | Archived"),
    search:   Optional[str] = Query(None, description="Full-text search across title and summary"),
    tag:      Optional[str] = Query(None, description="Filter by tag name"),
    page:     int           = Query(1, ge=1),
    page_size:int           = Query(20, ge=1, le=100),
    db=Depends(get_db),
):
    """
    List insights with optional filtering by sector, status, tag, and full-text search.
    Supports pagination.
    """
    conditions = []
    params     = []

    base_query = """
        SELECT
            i.id, i.title, i.summary, i.status,
            i.published_date,
            s.name AS sector,
            t.name AS trend, t.direction AS trend_direction,
            iss.title AS issue, iss.severity AS issue_severity,
            sol.title AS solution, sol.solution_type,
            sol.effectiveness AS solution_effectiveness,
            src.firm_name AS source_firm,
            src.report_title AS source_report,
            src.published_date AS report_date
        FROM insights i
        JOIN sectors s    ON i.sector_id   = s.id
        LEFT JOIN trends t    ON i.trend_id    = t.id
        LEFT JOIN issues iss  ON i.issue_id    = iss.id
        LEFT JOIN solutions sol ON i.solution_id = sol.id
        JOIN sources src  ON i.source_id   = src.id
    """

    if sector:
        conditions.append("s.name ILIKE %s")
        params.append(sector)
    if status:
        conditions.append("i.status = %s")
        params.append(status)
    if search:
        conditions.append(
            "to_tsvector('english', i.title || ' ' || i.summary) @@ plainto_tsquery('english', %s)"
        )
        params.append(search)
    if tag:
        conditions.append("""
            i.id IN (
                SELECT it.insight_id FROM insight_tags it
                JOIN tags tg ON tg.id = it.tag_id
                WHERE tg.name ILIKE %s
            )
        """)
        params.append(tag)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with db.cursor() as cur:
        # Total count
        cur.execute(f"SELECT COUNT(*) FROM ({base_query} {where_clause}) AS sub",
                    params)
        total = cur.fetchone()["count"]

        # Paginated results
        offset = (page - 1) * page_size
        cur.execute(
            f"{base_query} {where_clause} ORDER BY i.published_date DESC NULLS LAST "
            f"LIMIT %s OFFSET %s",
            params + [page_size, offset]
        )
        results = cur.fetchall()

    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "results":   results,
    }


@app.get("/insights/{insight_id}", response_model=InsightOut, tags=["Insights"])
def get_insight(insight_id: int, db=Depends(get_db)):
    """Retrieve a single insight by ID with full detail."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT
                i.id, i.title, i.summary, i.status, i.published_date,
                s.name AS sector,
                t.name AS trend, t.direction AS trend_direction,
                iss.title AS issue, iss.severity AS issue_severity,
                sol.title AS solution, sol.solution_type,
                sol.effectiveness AS solution_effectiveness,
                src.firm_name AS source_firm, src.report_title AS source_report,
                src.published_date AS report_date
            FROM insights i
            JOIN sectors s      ON i.sector_id   = s.id
            LEFT JOIN trends t      ON i.trend_id    = t.id
            LEFT JOIN issues iss    ON i.issue_id    = iss.id
            LEFT JOIN solutions sol ON i.solution_id = sol.id
            JOIN sources src    ON i.source_id   = src.id
            WHERE i.id = %s
        """, (insight_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Insight not found")
        return row


@app.get("/insights/{insight_id}/tags", response_model=list[TagOut], tags=["Insights"])
def get_insight_tags(insight_id: int, db=Depends(get_db)):
    """Get all tags attached to an insight."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT t.id, t.name, t.category
            FROM tags t
            JOIN insight_tags it ON it.tag_id = t.id
            WHERE it.insight_id = %s
            ORDER BY t.name
        """, (insight_id,))
        return cur.fetchall()


# ─────────────────────────────────────────────
# ROUTES — TRENDS
# ─────────────────────────────────────────────
@app.get("/trends", response_model=list[TrendTrackerOut], tags=["Trends"])
def list_trends(
    sector:    Optional[str] = Query(None),
    direction: Optional[str] = Query(None, description="Rising | Stable | Declining"),
    status:    Optional[str] = Query("Active"),
    db=Depends(get_db),
):
    """List trends with open issue counts — ideal for trend tracking view."""
    conditions = ["1=1"]
    params     = []

    if sector:
        conditions.append("s.name ILIKE %s")
        params.append(sector)
    if direction:
        conditions.append("t.direction = %s")
        params.append(direction)
    if status:
        conditions.append("t.status = %s")
        params.append(status)

    where = " AND ".join(conditions)

    with db.cursor() as cur:
        cur.execute(f"""
            SELECT
                t.id, s.name AS sector, t.name AS trend,
                t.direction, t.status, t.period_start,
                COUNT(iss.id) AS open_issues,
                src.firm_name AS source_firm
            FROM trends t
            JOIN sectors s  ON t.sector_id = s.id
            LEFT JOIN issues iss ON iss.trend_id = t.id AND iss.status = 'Active'
            JOIN sources src ON t.source_id = src.id
            WHERE {where}
            GROUP BY t.id, s.name, t.name, t.direction,
                     t.status, t.period_start, src.firm_name
            ORDER BY s.name, t.direction DESC
        """, params)
        return cur.fetchall()


# ─────────────────────────────────────────────
# ROUTES — ISSUES
# ─────────────────────────────────────────────
@app.get("/issues", response_model=list[IssueOut], tags=["Issues"])
def list_issues(
    sector:      Optional[str] = Query(None),
    min_severity:Optional[int] = Query(None, ge=1, le=5),
    status:      Optional[str] = Query("Active"),
    db=Depends(get_db),
):
    """List all issues, optionally filtered by sector and severity."""
    conditions = ["1=1"]
    params     = []

    if sector:
        conditions.append("s.name ILIKE %s")
        params.append(sector)
    if min_severity:
        conditions.append("iss.severity >= %s")
        params.append(min_severity)
    if status:
        conditions.append("iss.status = %s")
        params.append(status)

    where = " AND ".join(conditions)

    with db.cursor() as cur:
        cur.execute(f"""
            SELECT
                iss.id, s.name AS sector, t.name AS trend,
                iss.title, iss.description, iss.severity, iss.status
            FROM issues iss
            JOIN sectors s  ON iss.sector_id = s.id
            LEFT JOIN trends t ON iss.trend_id = t.id
            WHERE {where}
            ORDER BY iss.severity DESC
        """, params)
        return cur.fetchall()


# ─────────────────────────────────────────────
# ROUTES — SOLUTIONS
# ─────────────────────────────────────────────
@app.get("/solutions", response_model=list[SolutionOut], tags=["Solutions"])
def list_solutions(
    solution_type:   Optional[str] = Query(None),
    min_effectiveness: Optional[int] = Query(None, ge=1, le=5),
    db=Depends(get_db),
):
    """List all solutions, filterable by type and effectiveness."""
    conditions = ["1=1"]
    params     = []

    if solution_type:
        conditions.append("sol.solution_type = %s")
        params.append(solution_type)
    if min_effectiveness:
        conditions.append("sol.effectiveness >= %s")
        params.append(min_effectiveness)

    where = " AND ".join(conditions)

    with db.cursor() as cur:
        cur.execute(f"""
            SELECT
                sol.id, iss.title AS issue, sol.title,
                sol.description, sol.solution_type, sol.effectiveness,
                src.firm_name AS source_firm
            FROM solutions sol
            JOIN issues iss  ON sol.issue_id  = iss.id
            JOIN sources src ON sol.source_id = src.id
            WHERE {where}
            ORDER BY sol.effectiveness DESC
        """, params)
        return cur.fetchall()


# ─────────────────────────────────────────────
# ROUTES — REPORTS & ANALYTICS
# ─────────────────────────────────────────────
@app.get("/reports/sector-summary", response_model=list[SectorSummaryOut], tags=["Reports"])
def sector_summary(db=Depends(get_db)):
    """Aggregated sector stats for report generation."""
    with db.cursor() as cur:
        cur.execute("SELECT * FROM v_sector_summary ORDER BY sector")
        return cur.fetchall()


@app.get("/reports/rising-trends", response_model=list[TrendTrackerOut], tags=["Reports"])
def rising_trends(db=Depends(get_db)):
    """All currently rising trends across all sectors."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT
                t.id, s.name AS sector, t.name AS trend,
                t.direction, t.status, t.period_start,
                COUNT(iss.id) AS open_issues,
                src.firm_name AS source_firm
            FROM trends t
            JOIN sectors s  ON t.sector_id = s.id
            LEFT JOIN issues iss ON iss.trend_id = t.id AND iss.status = 'Active'
            JOIN sources src ON t.source_id = src.id
            WHERE t.direction = 'Rising' AND t.status = 'Active'
            GROUP BY t.id, s.name, t.name, t.direction,
                     t.status, t.period_start, src.firm_name
            ORDER BY s.name
        """)
        return cur.fetchall()


@app.get("/reports/critical-issues", response_model=list[IssueOut], tags=["Reports"])
def critical_issues(db=Depends(get_db)):
    """Issues with severity 4 or 5 across all sectors."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT iss.id, s.name AS sector, t.name AS trend,
                   iss.title, iss.description, iss.severity, iss.status
            FROM issues iss
            JOIN sectors s  ON iss.sector_id = s.id
            LEFT JOIN trends t ON iss.trend_id = t.id
            WHERE iss.severity >= 4 AND iss.status = 'Active'
            ORDER BY iss.severity DESC
        """)
        return cur.fetchall()


# ─────────────────────────────────────────────
# ROUTES — TAGS
# ─────────────────────────────────────────────
@app.get("/tags", response_model=list[TagOut], tags=["Tags"])
def list_tags(db=Depends(get_db)):
    """All available tags for filtering."""
    with db.cursor() as cur:
        cur.execute("SELECT id, name, category FROM tags ORDER BY name")
        return cur.fetchall()


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
