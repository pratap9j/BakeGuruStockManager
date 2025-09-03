import streamlit as st
import sqlite3
import pandas as pd
from fpdf import FPDF
import os
from datetime import datetime

# ============== DATABASE SETUP ===================
def init_db():
    conn = sqlite3.connect("bakeguru_stock.db")
    c = conn.cursor()

    # Products Table
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

    # Quotes Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_no TEXT,
            date TEXT,
            customer_name TEXT,
            company TEXT,
            address TEXT,
            phone TEXT,
            items TEXT,
            total REAL
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ============== HEADER ===================
def app_header():
    col1, col2 = st.columns([1, 6])
    with col1:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=60)
    with col2:
        st.markdown("<h1 style='margin-bottom:0;'>BakeGuru Stock Manager</h1>", unsafe_allow_html=True)
    st.markdown("---")

# ============== ADD STOCK ===================
def add_stock():
    app_header()
    st.subheader("📦 Add New Stock")

    with st.form("add_stock_form"):
        col1, col2 = st.columns(2)
        with col1:
            sku = st.text_input("SKU")
            name = st.text_input("Product Name")
            category = st.text_input("Category")
            subcategory = st.text_input("Subcategory")
        with col2:
            price = st.number_input("Price", min_value=0.0, step=0.01)
            stock = st.number_input("Stock Qty", min_value=0, step=1)
            image_url = st.text_input("Image URL (optional)")

        submitted = st.form_submit_button("Add Product")

        if submitted:
            conn = sqlite3.connect("bakeguru_stock.db")
            c = conn.cursor()
            try:
                c.execute("INSERT INTO products (sku, name, category, subcategory, price, stock, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (sku, name, category, subcategory, price, stock, image_url))
                conn.commit()
                st.success(f"✅ Product '{name}' added successfully!")
            except sqlite3.IntegrityError:
                st.error("❌ SKU already exists!")
            conn.close()

# ============== VIEW INVENTORY ===================
def view_inventory():
    app_header()
    st.subheader("📋 Inventory List")

    search = st.text_input("🔍 Search by SKU or Name")

    conn = sqlite3.connect("bakeguru_stock.db")
    df = pd.read_sql_query("SELECT * FROM products", conn)
    conn.close()

    if search:
        df = df[df["sku"].str.contains(search, case=False) | df["name"].str.contains(search, case=False)]

    if df.empty:
        st.warning("No products found.")
        return

    # Table with images and selection
    st.markdown("### Product List")
    for _, row in df.iterrows():
        col1, col2, col3, col4, col5, col6 = st.columns([1, 2, 2, 2, 1, 1])
        with col1:
            if row["image_url"]:
                st.image(row["image_url"], width=50)
            else:
                st.write("📦")
        with col2:
            st.write(f"**{row['sku']}**")
        with col3:
            st.write(row["name"])
        with col4:
            st.write(f"₹{row['price']}")
        with col5:
            st.write(f"{row['stock']} in stock")
        with col6:
            if st.button("➕ Add to Quote", key=f"addq_{row['id']}"):
                if "quote_cart" not in st.session_state:
                    st.session_state.quote_cart = []
                if row["id"] not in [item["id"] for item in st.session_state.quote_cart]:
                    st.session_state.quote_cart.append(row.to_dict())
                    st.success(f"Added {row['name']} to quote.")

# ============== QUOTE GENERATION ===================
def export_quote_pdf(df, customer, filename="quote.pdf"):
    pdf = FPDF()
    pdf.add_page()

    # Add Logo
    if os.path.exists("logo.png"):
        pdf.image("logo.png", x=10, y=8, w=25)  # Top-left logo

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, "BakeGuru Stock Manager - Quote", ln=True, align="C")
    pdf.ln(20)

    # Customer Info
    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 10, f"Customer: {customer['name']}", ln=True)
    pdf.cell(200, 10, f"Company: {customer['company']}", ln=True)
    pdf.cell(200, 10, f"Phone: {customer['phone']}", ln=True)
    pdf.multi_cell(200, 10, f"Address: {customer['address']}")
    pdf.ln(10)

    # Table Header
    pdf.set_font("Arial", "B", 10)
    pdf.cell(30, 10, "Image", 1)
    pdf.cell(30, 10, "SKU", 1)
    pdf.cell(50, 10, "Product", 1)
    pdf.cell(20, 10, "Qty", 1)
    pdf.cell(30, 10, "Price", 1)
    pdf.cell(30, 10, "Total", 1)
    pdf.ln()

    # Items
    pdf.set_font("Arial", "", 10)
    grand_total = 0
    for _, row in df.iterrows():
        qty = int(row.get("quote_qty", 1))
        total = qty * row["price"]
        grand_total += total

        # Image
        if row["image_url"] and os.path.exists(row["image_url"]):
            x_before = pdf.get_x()
            y_before = pdf.get_y()
            pdf.multi_cell(30, 10, "", 1)  # reserve cell space
            pdf.image(row["image_url"], x=x_before + 2, y=y_before + 2, w=15, h=15)
            pdf.set_xy(x_before + 30, y_before)
        else:
            pdf.cell(30, 10, "N/A", 1)

        pdf.cell(30, 10, str(row["sku"]), 1)
        pdf.cell(50, 10, str(row["name"]), 1)
        pdf.cell(20, 10, str(qty), 1)
        pdf.cell(30, 10, f"₹{row['price']}", 1)
        pdf.cell(30, 10, f"₹{total}", 1)
        pdf.ln()

    # Grand Total
    pdf.set_font("Arial", "B", 12)
    pdf.cell(160, 10, "Grand Total", 1)
    pdf.cell(30, 10, f"₹{grand_total}", 1)
    pdf.ln(20)

    # Disclaimer
    pdf.set_font("Arial", "I", 10)
    pdf.multi_cell(200, 10, "Note: GST & Shipping extra.")

    pdf.output(filename)
    return filename

def generate_quote():
    app_header()
    st.subheader("🧾 Generate Quote")

    if "quote_cart" not in st.session_state or not st.session_state.quote_cart:
        st.warning("No products added to quote.")
        return

    df = pd.DataFrame(st.session_state.quote_cart)
    df["quote_qty"] = df["quote_qty"] if "quote_qty" in df else 1

    edited_df = st.data_editor(
        df[["sku", "name", "price", "stock"]],
        column_config={
            "sku": "SKU",
            "name": "Product",
            "price": "Price",
            "stock": "Available Stock",
        },
        num_rows="dynamic",
        key="quote_editor"
    )

    # Customer Info
    with st.form("customer_form"):
        st.write("### Customer Info")
        customer_name = st.text_input("Customer Name")
        customer_company = st.text_input("Company")
        customer_phone = st.text_input("Phone")
        customer_address = st.text_area("Address")

        submitted = st.form_submit_button("Generate Quote")
        if submitted:
            customer = {
                "name": customer_name,
                "company": customer_company,
                "phone": customer_phone,
                "address": customer_address
            }
            filename = export_quote_pdf(edited_df, customer)
            st.success("✅ Quote generated successfully!")
            with open(filename, "rb") as f:
                st.download_button("📥 Download PDF", f, file_name=filename)

# ============== DASHBOARD ===================
def dashboard():
    app_header()
    st.subheader("📊 Dashboard Overview")

    conn = sqlite3.connect("bakeguru_stock.db")
    products = pd.read_sql_query("SELECT * FROM products", conn)
    conn.close()

    total_skus = len(products)
    total_in_stock = products["stock"].sum()
    total_out_of_stock = len(products[products["stock"] == 0])

    col1, col2, col3 = st.columns(3)
    col1.metric("Total SKUs", total_skus)
    col2.metric("Total In Stock", total_in_stock)
    col3.metric("Out of Stock", total_out_of_stock)

# ============== MAIN APP ===================
def main():
    st.set_page_config(page_title="BakeGuru Stock Manager", layout="wide")

    menu = ["Dashboard", "Add Stock", "Inventory", "Quotes"]
    choice = st.sidebar.selectbox("Navigation", menu)

    if choice == "Dashboard":
        dashboard()
    elif choice == "Add Stock":
        add_stock()
    elif choice == "Inventory":
        view_inventory()
    elif choice == "Quotes":
        generate_quote()

if __name__ == "__main__":
    main()
