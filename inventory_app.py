import os
import io
import base64
import time
import hashlib
import threading
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Tuple
import logging

import pandas as pd
from PIL import Image
import requests
import streamlit as st
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# =============================
# App Constants & Paths
# =============================
APP_TITLE = "BakeGuru Stock Manager"

DATA_DIR = os.getenv("BAKEGURU_DATA_DIR", "/mount/data" if os.path.isdir("/mount/data") else ".")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "bakeguru.db")
IMG_DIR = os.path.join(DATA_DIR, "images")
THUMB_DIR = os.path.join(IMG_DIR, "thumbs")
os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

# =============================
# SQLite Utilities (WAL + retry)
# =============================
_db_lock = threading.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@contextmanager
def db_conn(readonly: bool = False):
    abs_db = os.path.abspath(DB_PATH)
    uri = f"file:{abs_db}?mode={'ro' if readonly else 'rwc'}"
    con = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None, check_same_thread=False)
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=30000;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA foreign_keys=ON;")   # enable FK enforcement
        yield con
    finally:
        con.close()


def exec_sql(sql: str, params: Tuple = ()):  # write with retry
    with _db_lock:
        for attempt in range(5):
            try:
                with db_conn(False) as con:
                    con.execute("BEGIN;")
                    con.execute(sql, params)
                    con.execute("COMMIT;")
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise


def query_df(sql: str, params: Tuple = ()) -> pd.DataFrame:
    with db_conn(True) as con:
        return pd.read_sql_query(sql, con, params=params)


# =============================
# DB Init
# =============================
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    subcategory TEXT,
    price REAL NOT NULL DEFAULT 0,
    image_path TEXT,
    image_url TEXT,
    stock INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qno TEXT,
    customer_name TEXT,
    company TEXT,
    phone TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS quote_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    sku TEXT,
    name TEXT,
    qty INTEGER,
    price REAL,
    FOREIGN KEY(quote_id) REFERENCES quotes(id)
);
"""

with db_conn(False) as con:
    con.executescript(SCHEMA)

# =============================
# Image Helpers
# =============================

def _pil_to_data_url(img: Image.Image, ext: str = "JPEG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=ext)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    mime = "image/jpeg" if ext.upper() == "JPEG" else f"image/{ext.lower()}"
    return f"data:{mime};base64,{b64}"


def _save_thumb(img: Image.Image, basename: str) -> str:
    path = os.path.join(THUMB_DIR, f"{basename}.jpg")
    img.convert("RGB").save(path, format="JPEG", quality=85)
    return path


def ensure_thumb_from_path(path: str, key: str, size=(120, 120)) -> Tuple[Optional[str], Optional[str]]:
    try:
        if not os.path.exists(path):
            return None, None
        with Image.open(path) as im:
            im.thumbnail(size)
            thumb_name = f"{key}_thumb"
            thumb_path = _save_thumb(im, thumb_name)
            return _pil_to_data_url(im, "JPEG"), thumb_path
    except Exception as ex:
        logger.exception("ensure_thumb_from_path failed: %s", ex)
        return None, None


def ensure_thumb_from_url(url: str, key: str, size=(120, 120)) -> Tuple[Optional[str], Optional[str]]:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        with Image.open(io.BytesIO(r.content)) as im:
            im.thumbnail(size)
            thumb_name = f"{key}_urlthumb"
            thumb_path = _save_thumb(im, thumb_name)
            return _pil_to_data_url(im, "JPEG"), thumb_path
    except Exception as ex:
        logger.exception("ensure_thumb_from_url failed: %s", ex)
        return None, None


def save_uploaded_image(upload, sku: str) -> Optional[str]:
    try:
        ext = os.path.splitext(upload.name)[1].lower() or ".jpg"
        safe = "".join(c for c in sku if c.isalnum() or c in ("-","_"))
        fpath = os.path.join(IMG_DIR, f"{safe}{ext}")
        with open(fpath, "wb") as f:
            f.write(upload.getbuffer())
        return fpath
    except Exception:
        return None


# =============================
# PDF (FPDF2) Quote Builder
# =============================
class QuotePDF(FPDF):
    def header(self):
        self.set_font("helvetica", "B", 16)
        self.cell(0, 10, "BakeGuru Quote", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _pdf_output_bytes(pdf: FPDF) -> bytes:
    out = pdf.output(dest="S")
    return out.encode("latin1") if isinstance(out, str) else out


def render_quote_pdf(meta: dict, items: pd.DataFrame) -> bytes:
    pdf = QuotePDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    # Customer meta
    pdf.set_font("helvetica", size=12)
    pdf.cell(0, 8, f"Quote No: {meta.get('qno','')} ", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, f"Customer: {meta.get('name','')} | {meta.get('company','')}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, f"Phone: {meta.get('phone','')}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    # Table header (narrower layout, drop category/subcategory)
    col_w = {"img": 20, "sku": 28, "name": 90, "qty": 16, "price": 20, "total": 20}
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(col_w["img"], 8, "Img", border=1, align="C")
    pdf.cell(col_w["sku"], 8, "SKU", border=1)
    pdf.cell(col_w["name"], 8, "Name", border=1)
    pdf.cell(col_w["qty"], 8, "Qty", border=1, align="R")
    pdf.cell(col_w["price"], 8, "Price", border=1, align="R")
    pdf.cell(col_w["total"], 8, "Total", border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("helvetica", size=9)
    total = 0.0

    def row_height_for(name: str) -> int:
        lines = max(1, (len(name) // 40) + 1)
        return 20 if lines > 1 else 14

    for _, r in items.iterrows():
        qty = int(r.get("qty", 0) or 0)
        price = float(r.get("price", 0) or 0)
        line_total = qty * price
        total += line_total

        rh = row_height_for(str(r.get("name", "")))
        y0 = pdf.get_y()
        x0 = pdf.get_x()

        pdf.cell(col_w["img"], rh, "", border=1)
        img_path = r.get("thumb_path") or r.get("image_path")
        if not img_path and r.get("image_url"):
            key = r.get("sku") or hashlib.sha1(str(r.get("image_url")).encode()).hexdigest()[:10]
            _, img_path = ensure_thumb_from_url(str(r.get("image_url")), f"{key}_pdf")
        if img_path:
            try:
                pdf.image(img_path, x=x0 + 1.5, y=y0 + 1.5, w=col_w["img"] - 3)
            except Exception:
                pass
        pdf.set_xy(x0 + col_w["img"], y0)

        pdf.cell(col_w["sku"], rh, str(r.get("sku", ""))[:14], border=1)
        x1 = pdf.get_x(); y1 = pdf.get_y()
        pdf.multi_cell(col_w["name"], 6, str(r.get("name", "")), border=1)
        pdf.set_xy(x1 + col_w["name"], y0)
        pdf.cell(col_w["qty"], rh, str(qty), border=1, align="R")
        pdf.cell(col_w["price"], rh, f"{price:.2f}", border=1, align="R")
        pdf.cell(col_w["total"], rh, f"{line_total:.2f}", border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("helvetica", "B", 11)
    pdf.cell(0, 10, f"Grand Total: {total:.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "I", 9)
    pdf.cell(0, 7, "Prices are exclusive of taxes, unless specified.")

    return _pdf_output_bytes(pdf)


# =============================
# Streamlit UI (unchanged sections omitted for brevity)
# =============================
# ... rest of the code remains the same, but View Stock/Quote Builder editors can keep shorter widths ...
