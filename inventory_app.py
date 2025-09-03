import streamlit as st
import sqlite3
import pandas as pd
import os
from fpdf import FPDF
import requests
from io import BytesIO
from PIL import Image
from datetime import datetime

# ==============================
# Database Setup
# ==============================
DB_FILE = "bakeguru.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Inventory Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
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

    # Quotes Table
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
    conn.close()

init_db()

# ==============================
# Export Functions
# ==============================
def export_quote_excel(df, qno, name, company, addr, phone, total):
    os.makedirs("exports", exist_ok=True)
    file_path = f"exports/{qno}.xlsx"
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Quote")
        ws = writer.sheets["Quote"]
        ws.append([])
        ws.append(["Customer:", name])
        ws.append(["Company:", company])
        ws.append(["Address:", addr])
        ws.append(["Phone:", phone])
        ws.append([])
        ws.append(["Grand Total", total])
    return file_path


def export_quote_pdf(df, qno, name, company, addr, phone, total):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(200, 10, "BakeGuru Quote", ln=True, align="C")

    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 10, f"Quote No: {qno}", ln=True)
    pdf.cell(200, 10, f"Customer: {name}, {company}", ln=True)
    pdf.cell(200, 10, f"Address: {addr}", ln=True)
    pdf.cell(200, 10, f"Phone: {phone}", ln=True)

    pdf.ln(10)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(25, 8, "SKU", 1, align="C")
    pdf.cell(40, 8, "Name", 1, align="C")
    pdf.cell(20, 8, "Qty", 1, align="C")
    pdf.cell(25, 8, "Price", 1, align="C")
    pdf.cell(25, 8, "Total", 1, align="C")
    pdf.cell(40, 8, "Image", 1, ln=True, align="C")

    pdf.set_font("Arial", "", 10)

    for _, row in df.iterrows():
        pdf.cell(25, 20, str(row["sku"]), 1)
        pdf.cell(40, 20, str(row["name"]), 1)
        pdf.cell(20, 20, str(row["qty"]), 1, align="C")
        pdf.cell(25, 20, f"{row['price']:.2f}", 1, align="R")
        pdf.cell(25, 20, f"{row['qty'] * row['price']:.2f}", 1, align="R")

        if row["image_url"]:
            try:
                resp = requests.get(row["image_url"], timeout=5)
                img = Image.open(BytesIO(resp.content))
                img.thumbnail((30, 30))
                temp_file = f"temp_{row['id']}.png"
                img.save(temp_file)

                x = pdf.get_x()
                y = pdf.get_y()
                pdf.cell(40, 20, "", 1)  # placeholder
                pdf.image(temp_file, x=x+5, y=y+2, w=30, h=16)
                os.remove(temp_file)
            except:
                pdf.cell(40, 20, "N/A", 1, align="C")
        else:
            pdf.cell(40, 20, "N/A", 1, align="C")

        pdf.ln(20)

    pdf.ln(5)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(200, 10, f"Grand Total: {total:.2f}", ln=True, align="R")

    os.makedirs("exports", exist_ok=True)
    file_path = f"exports/{qno}.pdf"
    pdf.output(file_path)
    return file_path

# ==============================
# Streamlit App
# ==============================
st.set_page_config(page_title="BakeGuru Stock Manager", layout="wide")

menu = ["Inventory", "Add Stock", "Quote Builder", "Quotes History", "Dashboard"]
choice = st.sidebar.radio("Navigation", menu)

# ------------------------------
# Inventory View
# ------------------------------
if choice == "Inventory":
    st.title("📦 Inventory List")

    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM inventory", conn)
    conn.close()

    if not df.empty:
        search = st.text_input("🔎 Search (SKU / Name / Category)")
        if search:
            df = df[df.apply(lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1)]

        st.dataframe(df[["sku", "name", "category", "subcategory", "price", "stock", "image_url"]])
    else:
        st.info("No products found. Please add stock.")

# ------------------------------
# Add Stock
# ------------------------------
elif choice == "Add Stock":
    st.title("➕ Add / Upload Stock")

    with st.form("add_stock_form"):
        sku = st.text_input("SKU")
        name = st.text_input("Name")
        cat = st.text_input("Category")
        subcat = st.text_input("Subcategory")
        price = st.number_input("Price", min_value=0.0)
        stock = st.number_input("Stock", min_value=0)
        image_url = st.text_input("Image URL")

        submitted = st.form_submit_button("Add Stock")
        if submitted:
            conn = sqlite3.connect(DB_FILE)
            try:
                conn.execute("INSERT INTO inventory (sku,name,category,subcategory,price,stock,image_url) VALUES (?,?,?,?,?,?,?)",
                             (sku, name, cat, subcat, price, stock, image_url))
                conn.commit()
                st.success("✅ Stock Added")
            except Exception as e:
                st.error(f"Error: {e}")
            conn.close()

    st.subheader("📤 Bulk Upload via Excel")
    file_upload = st.file_uploader("Upload Excel file", type=["xlsx"])
    if file_upload:
        df_upload = pd.read_excel(file_upload)
        conn = sqlite3.connect(DB_FILE)
        df_upload.to_sql("inventory", conn, if_exists="append", index=False)
        conn.close()
        st.success("✅ Bulk upload complete!")

# ------------------------------
# Quote Builder
# ------------------------------
elif choice == "Quote Builder":
    st.title("📝 Quote Builder")

    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM inventory", conn)
    conn.close()

    if not df.empty:
        if "quote_cart" not in st.session_state:
            st.session_state["quote_cart"] = []

        st.subheader("Select Products")
        search = st.text_input("🔎 Search Products")
        if search:
            df = df[df.apply(lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1)]

        for _, row in df.iterrows():
            col1, col2, col3, col4, col5 = st.columns([2,3,2,2,2])
            with col1:
                st.write(row["sku"])
            with col2:
                st.write(row["name"])
            with col3:
                qty = st.number_input(f"Qty_{row['id']}", min_value=0, step=1, key=f"qty_{row['id']}")
            with col4:
                st.write(f"₹{row['price']}")
            with col5:
                if st.button("Add to Quote", key=f"add_{row['id']}"):
                    if qty > 0:
                        st.session_state["quote_cart"].append({**row, "qty": qty})
                        st.success(f"Added {row['name']} (x{qty})")

        if st.session_state["quote_cart"]:
            st.subheader("🛒 Current Quote Cart")
            cart_df = pd.DataFrame(st.session_state["quote_cart"])
            st.dataframe(cart_df[["sku","name","qty","price","image_url"]])

            if st.button("Generate Quote"):
                name = st.text_input("Customer Name")
                company = st.text_input("Company")
                addr = st.text_area("Address")
                phone = st.text_input("Phone")

                if st.button("Finalize & Save Quote"):
                    today = datetime.today().strftime("%Y-%m-%d")
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute("SELECT COUNT(*) FROM quotes")
                    qno = c.fetchone()[0] + 1
                    qid = f"BG-{qno:04d}"

                    items_json = pd.DataFrame(st.session_state["quote_cart"]).to_json(orient="records")
                    total = sum([item["qty"] * item["price"] for item in st.session_state["quote_cart"]])

                    c.execute("""INSERT INTO quotes (quote_no,date,customer_name,customer_company,customer_address,customer_phone,items,total)
                              VALUES (?,?,?,?,?,?,?,?)""",
                              (qid, today, name, company, addr, phone, items_json, total))
                    conn.commit()
                    conn.close()

                    pdf_file = export_quote_pdf(pd.DataFrame(st.session_state["quote_cart"]), qid, name, company, addr, phone, total)
                    excel_file = export_quote_excel(pd.DataFrame(st.session_state["quote_cart"]), qid, name, company, addr, phone, total)

                    st.success(f"✅ Quote {qid} saved!")
                    st.download_button("📥 Download PDF", open(pdf_file,"rb"), file_name=f"{qid}.pdf")
                    st.download_button("📥 Download Excel", open(excel_file,"rb"), file_name=f"{qid}.xlsx")

                    st.session_state["quote_cart"] = []

    else:
        st.warning("No products in stock.")

# ------------------------------
# Quotes History
# ------------------------------
elif choice == "Quotes History":
    st.title("📜 Quotes History")

    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM quotes", conn)
    conn.close()

    if not df.empty:
        st.dataframe(df[["quote_no","date","customer_name","total"]])
    else:
        st.info("No quotes found.")

# ------------------------------
# Dashboard
# ------------------------------
elif choice == "Dashboard":
    st.title("📊 Dashboard")

    conn = sqlite3.connect(DB_FILE)
    inv_df = pd.read_sql("SELECT * FROM inventory", conn)
    q_df = pd.read_sql("SELECT * FROM quotes", conn)
    conn.close()

    if not inv_df.empty:
        st.metric("Total SKUs", len(inv_df))
        st.metric("Total In Stock", inv_df["stock"].sum())
        st.metric("Out of Stock SKUs", (inv_df["stock"]==0).sum())
        st.write(inv_df.groupby("category")["sku"].count())
    else:
        st.info("No inventory data.")

    if not q_df.empty:
        st.metric("Total Quotes", len(q_df))
        q_df["date"] = pd.to_datetime(q_df["date"])
        st.line_chart(q_df.groupby(q_df["date"].dt.to_period("M")).size())
    else:
        st.info("No quotes data.")
