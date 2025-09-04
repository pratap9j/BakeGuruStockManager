import os
import json
import sqlite3
from datetime import datetime
from io import BytesIO

import pandas as pd
import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage

# =========================
# App config
# =========================
st.set_page_config(page_title="BakeGuru Stock Manager", layout="wide")

DB_FILE = "bakeguru.db"

# =========================
# Safe DB init (retain data)
# =========================
def init_db():
    fresh = not os.path.exists(DB_FILE)
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE,
            name TEXT,
            category TEXT,
            subcategory TEXT,
            price REAL,
            stock INTEGER,
            image_url TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_no TEXT,
            date TEXT,
            customer_name TEXT,
            customer_company TEXT,
            customer_address TEXT,
            customer_phone TEXT,
            items TEXT,
            total REAL
        )
    """)

    conn.commit()
    return conn

conn = init_db()

# =========================
# Header (simple)
# =========================
col1, col2 = st.columns([1, 6])
with col1:
    st.markdown("### 📦")
with col2:
    st.markdown(
        "<h1 style='margin-bottom:0;'>BakeGuru Stock Manager</h1>"
        "<div style='color:#666;margin-top:2px;'>Inventory • Quotes • Dashboard</div>",
        unsafe_allow_html=True,
    )
st.markdown("---")

# =========================
# Sidebar (list menu)
# =========================
st.sidebar.markdown("### 📂 Navigation")
MENU = ["Dashboard", "View Stock", "Add Stock", "Quote Builder", "Quotes History"]
choice = st.sidebar.radio("Go to", MENU, index=0)

# =========================
# Utils
# =========================
def load_products():
    return pd.read_sql("SELECT * FROM products", conn)

def safe_items_json(df: pd.DataFrame) -> str:
    """Avoid Unicode/ujson overflow by using json.dumps with ensure_ascii=False."""
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False)

# =========================
# Export: PDF with images
# =========================
def export_quote_pdf(df, qno, name, company, addr, phone, total):
    pdf = FPDF()
    pdf.add_page()

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, "BakeGuru Quote", ln=True, align="C")
    pdf.ln(8)

    # Customer info
    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 8, f"Quote No: {qno}", ln=True)
    pdf.cell(200, 8, f"Customer: {name} | {company}", ln=True)
    pdf.cell(200, 8, f"Phone: {phone}", ln=True)
    pdf.multi_cell(200, 8, f"Address: {addr}")
    pdf.ln(4)

    # Table header
    pdf.set_font("Arial", "B", 10)
    pdf.cell(30, 8, "SKU", 1, align="C")
    pdf.cell(50, 8, "Name", 1, align="C")
    pdf.cell(18, 8, "Qty", 1, align="C")
    pdf.cell(25, 8, "Price", 1, align="C")
    pdf.cell(25, 8, "Total", 1, align="C")
    pdf.cell(40, 8, "Image", 1, ln=True, align="C")

    pdf.set_font("Arial", "", 10)

    for _, row in df.iterrows():
        line_height = 22
        # main cells
        pdf.cell(30, line_height, str(row["sku"]), 1)
        pdf.cell(50, line_height, str(row["name"])[:40], 1)
        pdf.cell(18, line_height, str(int(row["qty"])), 1, align="C")
        pdf.cell(25, line_height, f"{row['price']:.2f}", 1, align="R")
        pdf.cell(25, line_height, f"{row['qty'] * row['price']:.2f}", 1, align="R")

        # image cell
        inserted = False
        if row.get("image_url"):
            try:
                resp = requests.get(row["image_url"], timeout=5)
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                img.thumbnail((30, 30))
                tempf = f"__tmp_{row['id']}.jpg"
                img.save(tempf, format="JPEG")
                x = pdf.get_x()
                y = pdf.get_y()
                pdf.cell(40, line_height, "", 1)  # reserve the cell
                pdf.image(tempf, x=x + 5, y=y + 3, w=30, h=16)
                os.remove(tempf)
                inserted = True
            except Exception:
                inserted = False
        if not inserted:
            pdf.cell(40, line_height, "N/A", 1, align="C")

        pdf.ln(line_height)

    pdf.ln(4)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(200, 10, f"Grand Total: {total:.2f}", ln=True, align="R")

    # disclaimer
    pdf.set_font("Arial", "I", 10)
    pdf.multi_cell(200, 8, "Note: GST & Shipping extra.")

    os.makedirs("exports", exist_ok=True)
    file_path = f"exports/{qno}.pdf"
    pdf.output(file_path)
    return file_path

# =========================
# Export: Excel with images
# =========================
def export_quote_excel(df, qno, name, company, addr, phone, total):
    os.makedirs("exports", exist_ok=True)
    file_path = f"exports/{qno}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Quote"

    headers = ["SKU", "Name", "Qty", "Price", "Total", "Image"]
    ws.append(headers)

    for idx, row in df.iterrows():
        excel_row = idx + 2  # 1-based + header
        ws.append([
            row["sku"],
            row["name"],
            int(row["qty"]),
            float(row["price"]),
            float(row["qty"] * row["price"]),
            ""  # image placeholder
        ])

        if row.get("image_url"):
            try:
                resp = requests.get(row["image_url"], timeout=5)
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                img.thumbnail((80, 80))
                tempf = f"__tmp_xl_{row['id']}.jpg"
                img.save(tempf, format="JPEG")
                xl_img = XLImage(tempf)
                xl_img.width, xl_img.height = 60, 60
                ws.add_image(xl_img, f"F{excel_row}")
                os.remove(tempf)
            except Exception:
                pass

    # widths
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 16

    # customer & total
    base = len(df) + 3
    ws[f"A{base}"] = "Customer:"
    ws[f"B{base}"] = name
    ws[f"A{base+1}"] = "Company:"
    ws[f"B{base+1}"] = company
    ws[f"A{base+2}"] = "Address:"
    ws[f"B{base+2}"] = addr
    ws[f"A{base+3}"] = "Phone:"
    ws[f"B{base+3}"] = phone

    ws[f"A{base+5}"] = "Grand Total"
    ws[f"B{base+5}"] = float(total)

    wb.save(file_path)
    return file_path

# =========================
# PAGES
# =========================
def page_dashboard():
    st.subheader("📊 Dashboard")

    inv = load_products()
    if inv.empty:
        st.info("No inventory yet.")
    else:
        total_skus = len(inv)
        total_in_stock = int(inv["stock"].sum())
        out_of_stock = int((inv["stock"] == 0).sum())

        c1, c2, c3 = st.columns(3)
        c1.metric("Total SKUs", total_skus)
        c2.metric("Total Units In Stock", total_in_stock)
        c3.metric("Out of Stock SKUs", out_of_stock)

        st.write("### SKUs per Category")
        cat_counts = inv["category"].fillna("Uncategorized").value_counts()
        if not cat_counts.empty:
            st.bar_chart(cat_counts)

    q = pd.read_sql("SELECT * FROM quotes", conn)
    if not q.empty:
        st.write("### Quotes per Month")
        q["date"] = pd.to_datetime(q["date"], errors="coerce")
        monthly = q.groupby(q["date"].dt.to_period("M")).size()
        st.line_chart(monthly)

def page_view_stock():
    st.subheader("📦 View Stock")

    df = load_products()
    if df.empty:
        st.info("No products available.")
        return

    # quick filter row
    cols = st.columns([3, 2, 2, 2])
    with cols[0]:
        term = st.text_input("🔎 Search (SKU / Name / Category)")
    with cols[1]:
        cat = st.selectbox("Category", ["All"] + sorted(df["category"].dropna().unique().tolist()))
    with cols[2]:
        subcat = st.selectbox("Subcategory", ["All"] + sorted(df["subcategory"].dropna().unique().tolist()))
    with cols[3]:
        low = st.checkbox("Low stock (< 5)")

    f = df.copy()
    if term:
        mask = f.apply(lambda r: r.astype(str).str.contains(term, case=False, na=False).any(), axis=1)
        f = f[mask]
    if cat != "All":
        f = f[f["category"] == cat]
    if subcat != "All":
        f = f[f["subcategory"] == subcat]
    if low:
        f = f[f["stock"] < 5]

    st.write("### Products")
    st.dataframe(f[["sku", "name", "category", "subcategory", "price", "stock", "image_url"]],
                 use_container_width=True)

def page_add_stock():
    st.subheader("➕ Add Stock")

    with st.form("add_stock_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            sku = st.text_input("SKU")
            name = st.text_input("Product Name")
            category = st.text_input("Category")
            subcategory = st.text_input("Subcategory")
        with c2:
            price = st.number_input("Price", min_value=0.0, step=0.01, format="%.2f")
            stock = st.number_input("Stock Quantity", min_value=0, step=1)
            image_url = st.text_input("Image URL (optional)")
        submitted = st.form_submit_button("Add Product")

        if submitted:
            if not sku or not name:
                st.error("Please provide at least SKU and Product Name.")
            else:
                try:
                    conn.execute(
                        "INSERT INTO products (sku, name, category, subcategory, price, stock, image_url) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (sku, name, category, subcategory, price, stock, image_url),
                    )
                    conn.commit()
                    st.success(f"✅ {name} added.")
                except sqlite3.IntegrityError:
                    st.error("SKU already exists. Try another SKU.")

    st.markdown("#### 📤 Bulk Upload (Excel .xlsx)")
    up = st.file_uploader("Upload with columns: sku,name,category,subcategory,price,stock,image_url", type=["xlsx"])
    if up:
        try:
            data = pd.read_excel(up)
            # Normalize headers (case-insensitive)
            data.columns = [c.strip().lower() for c in data.columns]
            req = ["sku", "name", "category", "subcategory", "price", "stock", "image_url"]
            missing = [c for c in req if c not in data.columns]
            if missing:
                st.error(f"Missing columns: {', '.join(missing)}")
            else:
                ok = 0
                for _, r in data.iterrows():
                    try:
                        conn.execute(
                            "INSERT INTO products (sku, name, category, subcategory, price, stock, image_url) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (str(r["sku"]), str(r["name"]), str(r["category"]), str(r["subcategory"]),
                             float(r["price"]), int(r["stock"]), str(r.get("image_url") or "")),
                        )
                        ok += 1
                    except sqlite3.IntegrityError:
                        pass
                conn.commit()
                st.success(f"✅ Bulk upload complete. Inserted {ok} rows.")
        except Exception as e:
            st.error(f"Upload failed: {e}")

def page_quote_builder():
    st.subheader("🧾 Quote Builder")

    df = load_products()
    if df.empty:
        st.info("No products in inventory.")
        return

    if "quote_cart" not in st.session_state:
        st.session_state.quote_cart = []

    # Search/filter
    s1, s2 = st.columns([3, 1])
    with s1:
        q = st.text_input("Search products (SKU/Name/Category)")
    with s2:
        st.write("")  # spacer

    filt = df.copy()
    if q:
        filt = filt[filt.apply(lambda r: r.astype(str).str.contains(q, case=False, na=False).any(), axis=1)]

    st.write("### Add Items")
    for _, row in filt.iterrows():
        c1, c2, c3, c4, c5 = st.columns([1.2, 3, 1.2, 1.2, 1.2])
        with c1:
            try:
                if row.get("image_url"):
                    st.image(row["image_url"], width=50)
                else:
                    st.write("📦")
            except Exception:
                st.write("📦")
        with c2:
            st.write(f"**{row['name']}**")
            st.caption(f"SKU: {row['sku']} • {row['category']}/{row['subcategory']}")
        with c3:
            st.write(f"₹{row['price']:.2f}")
        with c4:
            qty = st.number_input(f"Qty_{row['id']}", min_value=1,
                                  max_value=int(row['stock']) if row['stock'] else 9999,
                                  value=1, step=1, key=f"qty_{row['id']}")
        with c5:
            if st.button("Add to Quote", key=f"add_{row['id']}"):
                cart = st.session_state.quote_cart
                existing = next((i for i, it in enumerate(cart) if it["id"] == row["id"]), None)
                item = {**row.to_dict(), "qty": int(qty)}
                if existing is None:
                    cart.append(item)
                else:
                    cart[existing]["qty"] += int(qty)
                st.success(f"Added {row['name']} x{qty}")

    st.markdown("### 🛒 Current Quote Cart")
    if not st.session_state.quote_cart:
        st.info("No items added yet.")
        return

    cart_df = pd.DataFrame(st.session_state.quote_cart)
    cart_df["line_total"] = cart_df["qty"] * cart_df["price"]
    st.dataframe(cart_df[["sku", "name", "qty", "price", "line_total"]], use_container_width=True)
    grand_total = float(cart_df["line_total"].sum())
    st.markdown(f"**Grand Total:** ₹ {grand_total:,.2f}")

    st.markdown("---")
    st.markdown("### 👤 Customer Details")
    c1, c2 = st.columns(2)
    with c1:
        cust_name = st.text_input("Name")
        cust_company = st.text_input("Company")
    with c2:
        cust_phone = st.text_input("Phone")
        cust_addr = st.text_area("Address", height=92)

    cta1, cta2, cta3 = st.columns([1, 1, 4])
    with cta1:
        if st.button("🧾 Generate PDF"):
            today = datetime.today().strftime("%Y-%m-%d")
            qcount = pd.read_sql("SELECT COUNT(*) AS c FROM quotes", conn)["c"][0] + 1
            qno = f"BG-{qcount:04d}"

            items_df = pd.DataFrame(st.session_state.quote_cart)
            total = float((items_df["qty"] * items_df["price"]).sum())

            conn.execute(
                "INSERT INTO quotes (quote_no, date, customer_name, customer_company, customer_address, customer_phone, items, total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (qno, today, cust_name, cust_company, cust_addr, cust_phone, safe_items_json(items_df), total),
            )
            conn.commit()

            pdf_file = export_quote_pdf(items_df, qno, cust_name, cust_company, cust_addr, cust_phone, total)
            st.success(f"✅ Quote {qno} saved.")
            with open(pdf_file, "rb") as f:
                st.download_button("📥 Download PDF", f, file_name=f"{qno}.pdf")

    with cta2:
        if st.button("📊 Export Excel"):
            qcount = pd.read_sql("SELECT COUNT(*) AS c FROM quotes", conn)["c"][0] + 1
            qno = f"BG-{qcount:04d}"
            items_df = pd.DataFrame(st.session_state.quote_cart)
            total = float((items_df["qty"] * items_df["price"]).sum())
            xls = export_quote_excel(items_df, qno, cust_name, cust_company, cust_addr, cust_phone, total)
            with open(xls, "rb") as f:
                st.download_button("⬇️ Download Excel", f, file_name=f"{qno}.xlsx")

    st.markdown("#### 🗑 Manage Cart")
    rm_skus = st.multiselect("Remove items from cart", [f"{r['sku']} - {r['name']}" for r in st.session_state.quote_cart])
    if st.button("Remove Selected"):
        if rm_skus:
            st.session_state.quote_cart = [r for r in st.session_state.quote_cart
                                           if f"{r['sku']} - {r['name']}" not in rm_skus]
            st.success("Removed selected items.")

def page_quotes_history():
    st.subheader("📜 Quotes History")
    q = pd.read_sql("SELECT id, quote_no, date, customer_name, total FROM quotes ORDER BY id DESC", conn)
    if q.empty:
        st.info("No quotes yet.")
        return
    st.dataframe(q, use_container_width=True)

# =========================
# Router
# =========================
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
