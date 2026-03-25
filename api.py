"""
=============================================================
CONSULTANT INTELLIGENCE PLATFORM — REST API v2
=============================================================
Matches the signals schema from consultant_intelligence_v2.sql
Tables: sectors, sources, signals, tags, signal_tags
"""

import os
import logging
from datetime import date, datetime
from typing import Optional
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="Consultant Intelligence API",
    description="Signal intelligence platform for Legal, Medical, and AI sectors.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_config():
    url = os.getenv("DATABASE_URL")
    if url:
        p = urlparse(url)
        return {"host": p.hostname, "port": p.port or 5432,
                "dbname": p.path.lstrip("/"), "user": p.username, "password": p.password}
    return {"host": "localhost", "port": "5432", "dbname": "railway",
            "user": "postgres", "password": ""}

def get_db():
    conn = psycopg2.connect(**get_db_config(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

class SectorOut(BaseModel):
    id: int
    name: str
    color: Optional[str] = None

class SignalOut(BaseModel):
    id: int
    title: str
    summary: str
    what_it_means: Optional[str] = None
    why_it_matters: Optional[str] = None
    sector: str
    source: str
    source_tier: Optional[str] = None
    signal_type: str
    stage: str
    geography: Optional[str] = None
    published_date: Optional[date] = None
    is_bookmarked: bool = False
    tags: Optional[list] = []

class SignalListOut(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SignalOut]

class TagOut(BaseModel):
    id: int
    name: str

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/sectors", response_model=list[SectorOut], tags=["Sectors"])
def list_sectors(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT id, name, color FROM sectors ORDER BY name")
        return cur.fetchall()

@app.get("/tags", response_model=list[TagOut], tags=["Tags"])
def list_tags(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT id, name FROM tags ORDER BY name")
        return cur.fetchall()

def fetch_signals(sector, stage, search, tag, page, page_size, db):
    conditions = ["1=1"]
    params = []
    if sector:
        conditions.append("sec.name ILIKE %s")
        params.append(sector)
    if stage:
        conditions.append("sig.stage = %s")
        params.append(stage)
    if search:
        conditions.append("to_tsvector('english', sig.title || ' ' || sig.summary) @@ plainto_tsquery('english', %s)")
        params.append(search)
    if tag:
        conditions.append("sig.id IN (SELECT st.signal_id FROM signal_tags st JOIN tags t ON t.id = st.tag_id WHERE t.name ILIKE %s)")
        params.append(tag)
    where = " AND ".join(conditions)
    base = """
        SELECT sig.id, sig.title, sig.summary,
               sig.what_it_means, sig.why_it_matters,
               sec.name AS sector,
               src.name AS source, src.tier AS source_tier,
               sig.signal_type, sig.stage, sig.geography,
               sig.published_date, sig.is_bookmarked
        FROM signals sig
        JOIN sectors sec ON sig.sector_id = sec.id
        JOIN sources src ON sig.source_id = src.id
    """
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM ({base} WHERE {where}) s", params)
        total = cur.fetchone()["count"]
        offset = (page - 1) * page_size
        cur.execute(f"{base} WHERE {where} ORDER BY sig.published_date DESC NULLS LAST LIMIT %s OFFSET %s", params + [page_size, offset])
        rows = cur.fetchall()
    results = []
    for row in rows:
        row = dict(row)
        with db.cursor() as cur:
            cur.execute("SELECT t.name FROM tags t JOIN signal_tags st ON st.tag_id = t.id WHERE st.signal_id = %s", (row["id"],))
            row["tags"] = [r["name"] for r in cur.fetchall()]
        results.append(row)
    return {"total": total, "page": page, "page_size": page_size, "results": results}

@app.get("/signals", response_model=SignalListOut, tags=["Signals"])
def list_signals(sector: Optional[str]=Query(None), stage: Optional[str]=Query(None),
                 search: Optional[str]=Query(None), tag: Optional[str]=Query(None),
                 page: int=Query(1,ge=1), page_size: int=Query(20,ge=1,le=100), db=Depends(get_db)):
    return fetch_signals(sector, stage, search, tag, page, page_size, db)

@app.get("/insights", response_model=SignalListOut, tags=["Signals"])
def list_insights(sector: Optional[str]=Query(None), status: Optional[str]=Query(None),
                  search: Optional[str]=Query(None), tag: Optional[str]=Query(None),
                  page: int=Query(1,ge=1), page_size: int=Query(20,ge=1,le=100), db=Depends(get_db)):
    return fetch_signals(sector, status, search, tag, page, page_size, db)

@app.get("/signals/{signal_id}", response_model=SignalOut, tags=["Signals"])
def get_signal(signal_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("""
            SELECT sig.id, sig.title, sig.summary, sig.what_it_means, sig.why_it_matters,
                   sec.name AS sector, src.name AS source, src.tier AS source_tier,
                   sig.signal_type, sig.stage, sig.geography, sig.published_date, sig.is_bookmarked
            FROM signals sig
            JOIN sectors sec ON sig.sector_id = sec.id
            JOIN sources src ON sig.source_id = src.id
            WHERE sig.id = %s
        """, (signal_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Signal not found")
        row = dict(row)
        cur.execute("SELECT t.name FROM tags t JOIN signal_tags st ON st.tag_id = t.id WHERE st.signal_id = %s", (signal_id,))
        row["tags"] = [r["name"] for r in cur.fetchall()]
        return row

@app.get("/reports/sector-summary", tags=["Reports"])
def sector_summary(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("""
            SELECT sec.name AS sector, COUNT(sig.id) AS total_signals,
                   COUNT(sig.id) FILTER (WHERE sig.stage='Emerging') AS emerging,
                   COUNT(sig.id) FILTER (WHERE sig.stage='Developing') AS developing
            FROM sectors sec
            LEFT JOIN signals sig ON sig.sector_id = sec.id
            GROUP BY sec.name ORDER BY sec.name
        """)
        return cur.fetchall()

@app.get("/reports/emerging", tags=["Reports"])
def emerging_signals(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("""
            SELECT sig.id, sig.title, sig.summary, sec.name AS sector,
                   sig.signal_type, sig.stage, sig.published_date
            FROM signals sig JOIN sectors sec ON sig.sector_id = sec.id
            WHERE sig.stage = 'Emerging'
            ORDER BY sig.published_date DESC NULLS LAST LIMIT 20
        """)
        return cur.fetchall()
