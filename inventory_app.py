import os
import io
import base64
import time
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
DB_PATH = "bakeguru.db"
IMG_DIR = "images"
THUMB_DIR = os.path.join(IMG_DIR, "thumbs")
os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

# =============================
# SQLite Utilities (WAL + retry)
# =============================
_db_lock = threading.Lock()

@contextmanager
def db_conn(readonly: bool = False):
    # Use timeout + WAL + busy_timeout to avoid 'database is locked'
    uri = f"file:{DB_PATH}?mode={'ro' if readonly else 'rwc'}"
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
# Image Helpers (Data URL thumbs)
# =============================

def _pil_to_data_url(img: Image.Image, ext: str = "JPEG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=ext)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    mime = "image/jpeg" if ext.upper() == "JPEG" else f"image/{ext.lower()}"
    return f"data:{mime};base64,{b64}"


def make_thumb_from_path(path: str, size=(96, 96)) -> Optional[str]:
    try:
        im = Image.open(path)
        im.convert("RGB")
        im.thumbnail(size)
        return _pil_to_data_url(im, "JPEG")
    except Exception:
        return None


def make_thumb_from_url(url: str, size=(96, 96)) -> Optional[str]:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        im.convert("RGB")
        im.thumbnail(size)
        return _pil_to_data_url(im, "JPEG")
    except Exception:
        return None


def save_uploaded_image(upload, sku: str) -> Optional[str]:
    try:
        ext = os.path.splitext(upload.name)[1].lower() or ".jpg"
        fpath = os.path.join(IMG_DIR, f"{sku}{ext}")
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


def render_quote_pdf(meta: dict, items: pd.DataFrame) -> bytes:
    pdf = QuotePDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    # Customer meta
    pdf.set_font("helvetica", size=12)
    pdf.cell(0, 8, f"Quote No: {meta['qno']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, f"Customer: {meta['name']} | {meta['company']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 8, f"Phone: {meta['phone']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    # Table header
    col_w = {"img": 24, "sku": 35, "name": 70, "qty": 20, "price": 20, "total": 21}
    pdf.set_font("helvetica", "B", 10)
    pdf.cell(col_w["img"], 8, "Image", border=1, align="C")
    pdf.cell(col_w["sku"], 8, "SKU", border=1)
    pdf.cell(col_w["name"], 8, "Name", border=1)
    pdf.cell(col_w["qty"], 8, "Qty", border=1, align="R")
    pdf.cell(col_w["price"], 8, "Price", border=1, align="R")
    pdf.cell(col_w["total"], 8, "Total", border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("helvetica", size=10)
    total = 0.0

    def row_height_for(name: str) -> int:
        # modest multi-line name support
        lines = max(1, (len(name) // 38) + 1)
        return 24 if lines > 1 else 18

    for _, r in items.iterrows():
        qty = int(r.get("qty", 0) or 0)
        price = float(r.get("price", 0) or 0)
        line_total = qty * price
        total += line_total

        rh = row_height_for(str(r.get("name", "")))
        y0 = pdf.get_y()
        x0 = pdf.get_x()

        # --- Image FIRST (top-left of the row) ---
        pdf.cell(col_w["img"], rh, "", border=1)
        # Draw image inside the image cell
        img_dataurl = r.get("thumb", None) or r.get("image_url", None)
        if img_dataurl and isinstance(img_dataurl, str):
            try:
                # place image within the image cell rectangle
                pdf.image(img_dataurl, x=x0 + 2, y=y0 + 2, w=col_w["img"] - 4)
            except Exception:
                pass
        pdf.set_xy(x0 + col_w["img"], y0)

        # --- Other cells ---
        pdf.cell(col_w["sku"], rh, str(r.get("sku", "")), border=1)
        # Name as multicell within a fixed box
        x1 = pdf.get_x(); y1 = pdf.get_y()
        pdf.multi_cell(col_w["name"], 6, str(r.get("name", "")), border=1)
        # Align following cells to the row's top
        pdf.set_xy(x1 + col_w["name"], y0)
        pdf.cell(col_w["qty"], rh, str(qty), border=1, align="R")
        pdf.cell(col_w["price"], rh, f"{price:.2f}", border=1, align="R")
        pdf.cell(col_w["total"], rh, f"{line_total:.2f}", border=1, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, f"Grand Total: {total:.2f}", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("helvetica", "I", 10)
    pdf.cell(0, 8, "Prices are exclusive of taxes, unless specified.")

    return pdf.output(dest="S").encode("latin1")


# =============================
# Streamlit UI
# =============================
st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="wide")
st.title(APP_TITLE)

with st.sidebar:
    st.markdown("## Navigation")
    choice = st.radio("Go to", ["Dashboard", "View Stock", "Add Stock", "Quote Builder", "Quotes History"], index=1)

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

    # build thumbnail as DATA URL string so pandas treats it as string (no bytes -> no UnicodeDecodeError)
    thumbs = []
    for _, r in df.iterrows():
        url = (r["image_url"] or "").strip()
        pth = (r["image_path"] or "").strip()
        dataurl = None
        if pth and os.path.exists(pth):
            dataurl = make_thumb_from_path(pth)
        if not dataurl and url:
            dataurl = make_thumb_from_url(url)
        thumbs.append(dataurl)
    df.insert(1, "thumb", thumbs)

    st.markdown("### Products (inline editable)")
    edited = st.data_editor(
        df[["thumb", "sku", "name", "category", "subcategory", "price", "stock", "reorder_level", "image_url", "image_path"]],
        column_config={
            "thumb": st.column_config.ImageColumn("Image", help="Local uploads or URL-based", width="content"),
            "price": st.column_config.NumberColumn("Price", format="₹%.2f"),
            "stock": st.column_config.NumberColumn("Stock"),
            "reorder_level": st.column_config.NumberColumn("Reorder Level"),
            "image_url": st.column_config.TextColumn("Image URL"),
            "image_path": st.column_config.TextColumn("Image Path", help="Saved on server"),
        },
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
    )

    if st.button("💾 Save Changes"):
        # Persist back only allowed fields
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
        sku = st.text_input("SKU *").strip()
        name = st.text_input("Name *").strip()
        category = st.text_input("Category")
        subcategory = st.text_input("Subcategory")
        price = st.number_input("Price", min_value=0.0, step=0.5)
        col1, col2 = st.columns(2)
        with col1:
            image_url = st.text_input("Image URL")
        with col2:
            upload = st.file_uploader("Upload Image", type=["jpg", "jpeg", "png", "webp"])  # optional
        stock = st.number_input("Stock", min_value=0, step=1)
        reorder = st.number_input("Reorder Level", min_value=0, step=1)

        submitted = st.form_submit_button("Add / Update")

    if submitted:
        if not sku or not name:
            st.error("SKU and Name are required")
            return

        img_path = None
        if upload is not None:
            img_path = save_uploaded_image(upload, sku)
            if not img_path:
                st.warning("Image save failed (continuing without image)")

        # Upsert
        exec_sql(
            """
            INSERT INTO products (sku, name, category, subcategory, price, image_path, image_url, stock, reorder_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
              name=excluded.name,
              category=excluded.category,
              subcategory=excluded.subcategory,
              price=excluded.price,
              image_path=COALESCE(excluded.image_path, products.image_path),
              image_url=COALESCE(excluded.image_url, products.image_url),
              stock=excluded.stock,
              reorder_level=excluded.reorder_level
            """,
            (sku, name, category, subcategory, float(price), img_path, image_url, int(stock), int(reorder))
        )
        st.success("Product saved.")


# ---------- Quote Builder ----------

def page_quote_builder():
    st.subheader("Quote Builder")
    df = query_df("SELECT sku, name, price, image_url, image_path FROM products ORDER BY name")

    pick = st.multiselect("Select items", df["name"].tolist())
    if pick:
        cart = df[df["name"].isin(pick)].copy().reset_index(drop=True)
        cart["qty"] = 1
        # thumbnails for preview
        thumbs = []
        for _, r in cart.iterrows():
            du = None
            if r["image_path"] and os.path.exists(r["image_path"]):
                du = make_thumb_from_path(r["image_path"]) 
            if not du and r["image_url"]:
                du = make_thumb_from_url(r["image_url"]) 
            thumbs.append(du)
        cart.insert(0, "thumb", thumbs)

        cart = st.data_editor(
            cart[["thumb", "sku", "name", "price", "qty", "image_url"]],
            column_config={
                "thumb": st.column_config.ImageColumn("Image", width="content"),
                "price": st.column_config.NumberColumn("Price", format="₹%.2f"),
                "qty": st.column_config.NumberColumn("Qty", min_value=1, step=1),
                "image_url": st.column_config.TextColumn("Image URL"),
            },
            hide_index=True,
            width="stretch",
        )

        # PDF Meta
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
            pdf_bytes = render_quote_pdf(meta, cart)
            st.download_button("Download Quote PDF", data=pdf_bytes, file_name=f"{qno}.pdf", mime="application/pdf")

            # Save quote summary (optional)
            exec_sql(
                "INSERT INTO quotes (qno, customer_name, company, phone, created_at) VALUES (?, ?, ?, ?, ?)",
                (qno, cname, comp, phone, datetime.now().isoformat(timespec="seconds"))
            )
            qid = query_df("SELECT id FROM quotes WHERE qno=? ORDER BY id DESC LIMIT 1", (qno,)).iloc[0,0]
            for _, r in cart.iterrows():
                exec_sql(
                    "INSERT INTO quote_items (quote_id, sku, name, qty, price) VALUES (?, ?, ?, ?, ?)",
                    (qid, r["sku"], r["name"], int(r["qty"]), float(r["price"]))
                )
            st.success("Quote saved to history.")


# ---------- Quotes History ----------

def page_quotes_history():
    h = query_df("SELECT id, qno, customer_name, company, phone, created_at FROM quotes ORDER BY id DESC")
    st.dataframe(h, use_container_width=True)


# ---------- Router ----------
if choice == "Dashboard":
    page_dashboard()
elif choice == "View Stock":
    page_view_stock()
elif choice == "Add Stock":
    page_add_stock()
elif choice == "Quote Builder":
    page_quote_builder()
elif choice == "Quotes History":
    page_quotes_history()
