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

# Writable dir: /mount/data on Streamlit Cloud; current dir locally
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

@contextmanager
def db_conn(readonly: bool = False):
    """Context-managed SQLite connection with WAL, timeouts & retries."""
    abs_db = os.path.abspath(DB_PATH)
    uri = f"file:{abs_db}?mode={'ro' if readonly else 'rwc'}"
    con = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None, check_same_thread=False)
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=30000;")
        con.execute("PRAGMA synchronous=NORMAL;")
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
# Image Helpers (file-cached thumbs + data URLs)
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
        im = Image.open(path)
        im.thumbnail(size)
        thumb_name = f"{key}_thumb"
        thumb_path = _save_thumb(im, thumb_name)
        return _pil_to_data_url(im, "JPEG"), thumb_path
    except Exception:
        return None, None


def ensure_thumb_from_url(url: str, key: str, size=(120, 120), refresh: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """Return (dataurl, thumb_path) for a remote image URL with simple on-disk cache & retry.

    Cache file name is derived from URL hash + key. Set refresh=True to bypass cache.
    """
    try:
        # Cache path
        url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        thumb_name = f"{key}_{url_hash}_urlthumb"
        cache_path = os.path.join(THUMB_DIR, f"{thumb_name}.jpg")

        if (not refresh) and os.path.exists(cache_path):
            im = Image.open(cache_path)
            return _pil_to_data_url(im, "JPEG"), cache_path

        # Simple retry loop for flaky URLs
        last_err = None
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                im = Image.open(io.BytesIO(r.content))
                im.thumbnail(size)
                im.convert("RGB").save(cache_path, format="JPEG", quality=85)
                return _pil_to_data_url(im, "JPEG"), cache_path
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        # Retries exhausted
        raise last_err if last_err else RuntimeError("unknown fetch error")
    except Exception:
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

    # Table header (narrower layout)
    col_w = {"img": 18, "sku": 28, "name": 68, "qty": 16, "price": 18, "total": 20}
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

        # Image first
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

        # Other cells
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
# Streamlit UI
# =============================
st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")
st.title(APP_TITLE)

with st.sidebar:
    st.empty()  # replaced placeholder with empty widget
    choice = st.radio(
        "Go to",
        [
            "Dashboard",
            "View Stock",
            "Add Stock",
            "Quote Builder",
            "Quotes History",
            "Diagnostics",
        ],
        index=1,
    )

# ---------- Dashboard ----------

def page_dashboard():
    a = query_df("SELECT COUNT(*) as n FROM products")
    b = query_df("SELECT SUM(stock) as s FROM products")
    c = query_df("SELECT SUM(price*stock) as v FROM products")

    c1, c2, c3 = st.columns(3)
    c1.metric("Products", int(a.iloc[0,0] or 0))
    c2.metric("Units in Stock", int(b.iloc[0,0] or 0))
    c3.metric("Inventory Value", f"₹{(c.iloc[0,0] or 0):,.2f}")


# ---------- View Stock (inline edit + thumbnails) ----------

def page_view_stock():
    df = query_df("SELECT sku, name, category, subcategory, price, image_path, image_url, stock, reorder_level FROM products ORDER BY name")

    # Refresh URL thumbnails cache on demand
    colr1, _ = st.columns([1, 6])
    with colr1:
        refresh_thumbs = st.button("🔄 Refresh thumbs")

    # Build thumbnail dataURL for UI + file path for PDF
    thumb_dataurls = []
    thumb_paths = []
    for _, r in df.iterrows():
        sku = (r["sku"] or "").strip() or hashlib.sha1(str(r.to_dict()).encode()).hexdigest()[:10]
        dataurl = None
        fpath = None
        pth = (r["image_path"] or "").strip()
        url = (r["image_url"] or "").strip()
        if pth:
            dataurl, fpath = ensure_thumb_from_path(pth, sku)
        if not dataurl and url:
            dataurl, fpath = ensure_thumb_from_url(url, sku, refresh=refresh_thumbs)
        thumb_dataurls.append(dataurl)
        thumb_paths.append(fpath)
    df.insert(1, "thumb", thumb_dataurls)
    df.insert(2, "thumb_path", thumb_paths)

    st.empty()  # replaced placeholder with empty widget
    edited = st.data_editor(
        # Hide thumb_path in the UI but keep it in df for PDF
        df[["thumb", "sku", "name", "category", "subcategory", "price", "stock", "reorder_level", "image_url", "image_path"]],
        column_config={
            "thumb": st.column_config.ImageColumn("Img", help="Local uploads or URL-based", width="small"),
            "sku": st.column_config.TextColumn("SKU", width="small"),
            "name": st.column_config.TextColumn("Name", width="medium"),
            "category": st.column_config.TextColumn("Cat", width="small"),
            "subcategory": st.column_config.TextColumn("Subcat", width="small"),
            "price": st.column_config.NumberColumn("Price", format="₹%.2f", width="small"),
            "stock": st.column_config.NumberColumn("Stock", width="small"),
            "reorder_level": st.column_config.NumberColumn("Reorder", width="small"),
            "image_url": st.column_config.TextColumn("Image URL", width="medium"),
            "image_path": st.column_config.TextColumn("Image Path", width="medium"),
        },
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
    )

    if st.button("💾 Save Changes"):
        for _, r in edited.iterrows():
            exec_sql(
                """
                UPDATE products
                SET name=?, category=?, subcategory=?, price=?, stock=?, reorder_level=?, image_url=?, image_path=?
                WHERE sku=?
                """,
                (
                    r["name"], r["category"], r["subcategory"], float(r["price"] or 0), int(r["stock"] or 0),
                    int(r["reorder_level"] or 0), (r["image_url"] or None), (r["image_path"] or None), r["sku"]
                )
            )
        st.success("Changes saved.")


# ---------- Add Stock ----------

def page_add_stock():
    st.subheader("Add / Update Product")
    with st.form("add_form"):
        c
