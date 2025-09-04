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
    image_url TEXT,
    stock INTEGER NOT NULL DEFAULT 0
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


def ensure_thumb_from_url(url: str, key: str, size=(120, 120), refresh: bool = False) -> Tuple[Optional[str], Optional[str]]:
    try:
        url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        thumb_name = f"{key}_{url_hash}_urlthumb"
        cache_path = os.path.join(THUMB_DIR, f"{thumb_name}.jpg")

        if (not refresh) and os.path.exists(cache_path):
            im = Image.open(cache_path)
            return _pil_to_data_url(im, "JPEG"), cache_path

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
        raise last_err if last_err else RuntimeError("unknown fetch error")
    except Exception:
        return None, None


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
    if isinstance(out, bytearray):
        return bytes(out)
    if isinstance(out, str):
        return out.encode("latin1")
    return out


# =============================
# Streamlit UI
# =============================
st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")
st.title(APP_TITLE)

with st.sidebar:
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


# ---------- View Stock ----------

def page_view_stock():
    df = query_df("SELECT sku, name, category, subcategory, price, image_url, stock FROM products ORDER BY name")

    colr1, _ = st.columns([1, 6])
    with colr1:
        refresh_thumbs = st.button("🔄 Refresh thumbs")

    thumb_dataurls = []
    thumb_paths = []
    for _, r in df.iterrows():
        sku = (r["sku"] or "").strip() or hashlib.sha1(str(r.to_dict()).encode()).hexdigest()[:10]
        dataurl, fpath = (None, None)
        url = (r["image_url"] or "").strip()
        if url:
            dataurl, fpath = ensure_thumb_from_url(url, sku, refresh=refresh_thumbs)
        thumb_dataurls.append(dataurl)
        thumb_paths.append(fpath)
    df.insert(1, "thumb", thumb_dataurls)
    df.insert(2, "thumb_path", thumb_paths)

    edited = st.data_editor(
        df[["thumb", "sku", "name", "category", "subcategory", "price", "stock", "image_url"]],
        column_config={
            "thumb": st.column_config.ImageColumn("Img", help="Product image", width="small"),
            "sku": st.column_config.TextColumn("SKU", width="small"),
            "name": st.column_config.TextColumn("Name", width="medium"),
            "category": st.column_config.TextColumn("Cat", width="small"),
            "subcategory": st.column_config.TextColumn("Subcat", width="small"),
            "price": st.column_config.NumberColumn("Price", format="₹%.2f", width="small"),
            "stock": st.column_config.NumberColumn("Stock", width="small"),
            "image_url": st.column_config.TextColumn("Image URL", width="medium"),
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
                SET name=?, category=?, subcategory=?, price=?, stock=?, image_url=?
                WHERE sku=?
                """,
                (
                    r["name"], r["category"], r["subcategory"], float(r["price"] or 0), int(r["stock"] or 0),
                    (r["image_url"] or None), r["sku"]
                )
            )
        st.success("Changes saved.")


# ---------- Add Stock ----------

def page_add_stock():
    st.subheader("Add / Update Product")
    with st.form("add_form"):
        c1, c2, c3, c4, c5 = st.columns([1, 2, 1, 1, 1])
        with c1:
            sku = st.text_input("SKU *", placeholder="SKU-001")
        with c2:
            name = st.text_input("Name *", placeholder="Paper Cake Box 8x8")
        with c3:
            category = st.text_input("Category", placeholder="Packaging")
        with c4:
            subcategory = st.text_input("Subcat", placeholder="Cake Box")
        with c5:
            price = st.number_input("Price", min_value=0.0, step=0.5)

        c6, c7 = st.columns([2, 2])
        with c6:
            image_url = st.text_input("Image URL", placeholder="https://...")
        with c7:
            stock = st.number_input("Stock", min_value=0, step=1)

        submitted = st.form_submit_button("Add / Update")

    if submitted:
        if not sku or not name:
            st.error("SKU and Name are required")
            return

        exec_sql(
            """
            INSERT INTO products (sku, name, category, subcategory, price, image_url, stock)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
              name=excluded.name,
              category=excluded.category,
              subcategory=excluded.subcategory,
              price=excluded.price,
              image_url=excluded.image_url,
              stock=excluded.stock
            """,
            (sku.strip(), name.strip(), category.strip(), subcategory.strip(), float(price), image_url.strip(), int(stock))
        )
        st.success("Product saved.")


# ---------- Quote Builder ----------

def page_quote_builder():
    st.subheader("Quote Builder")
    df = query_df("SELECT sku, name, price, image_url FROM products ORDER BY name")

    pick = st.multiselect("Select items", df["name"].tolist())
    if pick:
        cart = df[df["name"].isin(pick)].copy().reset_index(drop=True)
        cart["qty"] = 1

        durls, tpaths = [], []
        for _, r in cart.iterrows():
            sku = (r["sku"] or "").strip() or hashlib.sha1(str(r.to_dict()).encode()).hexdigest()[:10]
            durl, tpath = (None, None)
            if r["image_url"]:
                durl, tpath = ensure_thumb_from_url(r["image_url"], f"{sku}_q")
            durls.append(durl)
            tpaths.append(tpath)
        cart.insert(0, "thumb", durls)
        cart["thumb_path"] = tpaths

        cart = st.data_editor(
            cart[["thumb", "sku", "name", "price", "qty", "image_url"]],
            column_config={
                "thumb": st.column_config.ImageColumn("Img", width="small"),
                "sku": st.column_config.TextColumn("SKU", width="small"),
                "name": st.column_config.TextColumn("Name", width="medium"),
                "price": st.column_config.NumberColumn("Price", format="₹%.2f", width="small"),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1, width="small"),
                "image_url": st.column_config.TextColumn("Image URL", width="medium"),
            },
            hide_index=True,
            width="stretch",
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            qno = st.text_input("Quote No", value=f"Q{datetime.now():%Y%m%d-%H%M}")
        with c2:
            cname = st.text_input("Customer Name")
        with c3:
            comp = st.text_input("Company")
        phone = st.text_input("Phone")

        if st.button("📄 Generate PDF"):
            meta = {"qno": qno, "name": cname, "company": comp, "phone": phone}
            try:
                pdf_bytes = render_quote_pdf(meta, cart)
                st.session_state["last_pdf"] = (qno, pdf_bytes)
                st.success("PDF generated.")
            except Exception as e:
                st.error(f"PDF generation failed: {e}")

        if "last_pdf" in st.session_state:
            last_qno, last_bytes = st.session_state["last_pdf"]
            st.download_button(
                "⬇️ Download Quote PDF",
                data=last_bytes,
                file_name=f"{last_qno}.pdf",
                mime="application/pdf",
                key="download_pdf_btn",
            )


# ---------- Quotes History ----------

def page_quotes_history():
    h = query_df("SELECT id, qno, customer_name, company, phone, created_at FROM quotes ORDER BY id DESC")
    st.dataframe(h, use_container_width=True)


# ---------- Diagnostics ----------

def page_diagnostics():
    st.subheader("Diagnostics & Self-Tests")
    st.caption("Quick checks to validate environment, DB, images, and PDF generation.")

    st.write("**DATA_DIR:**", DATA_DIR)
    st.write("**DB_PATH:**", DB_PATH)
    st.write("**IMG_DIR:**", IMG_DIR)

    try:
        testfile = os.path.join(DATA_DIR, "_write_test.txt")
        with open(testfile, "w", encoding="utf-8") as f:
            f.write("ok")
        st.success("FS write OK")
    except Exception as e:
        st.error(f"FS write FAILED: {e}")

    try:
        with db_conn() as con:
            con.execute("CREATE TABLE IF NOT EXISTS _ping (id INTEGER PRIMARY KEY)")
            con.execute("INSERT INTO _ping DEFAULT VALUES")
        st.success("DB write OK")
    except Exception as e:
        st.error(f"DB write FAILED: {e}")

    try:
        test_items = pd.DataFrame([
            {"sku":"TEST-1","name":"Sample Product","qty":2,"price":99.5,"thumb_path":None,"image_url":None},
        ])
        pdf_bytes = render_quote_pdf({"qno":"TEST","name":"QA","company":"BakeGuru","phone":""}, test_items)
        st.download_button("Download Test PDF", data=pdf_bytes, file_name="test.pdf", mime="application/pdf")
        st.success("PDF generation OK")
    except Exception as e:
        st.error(f"PDF generation FAILED: {e}")

