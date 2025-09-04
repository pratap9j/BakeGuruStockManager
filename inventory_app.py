import streamlit as st
import sqlite3
import pandas as pd
import os
import datetime
from fpdf import FPDF

# ========== DB INIT ==========
def init_db():
    conn = sqlite3.connect("inventory.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS inventory
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 sku TEXT, name TEXT, category TEXT, subcategory TEXT,
                 price REAL, stock INTEGER, image_url TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS quotes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 quote_no TEXT, date TEXT, customer_name TEXT,
                 company TEXT, address TEXT, phone TEXT,
                 items TEXT, total REAL)''')
    conn.commit()
    return conn

conn = init_db()

# ========== HEADER ==========
col1, col2 = st.columns([1, 6])
with col1:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=80)
with col2:
    st.markdown(
        "<h1 style='margin-bottom:0;'>BakeGuru Stock Manager</h1>",
        unsafe_allow_html=True
    )
st.markdown("<hr>", unsafe_allow_html=True)

# ========== SIDEBAR NAV ==========
st.sidebar.image("logo.png", width=120)
st.sidebar.markdown("### BakeGuru Stock Manager")
st.sidebar.markdown("---")

menu_items = [
    "Dashboard",
    "View Stock",
    "Add Stock",
    "Quotes",
    "User Management",
    "Logout"
]

choice = st.sidebar.radio("📂 Navigation", menu_items)

# ========== FUNCTIONS ==========

def show_dashboard():
    st.subheader("📊 Dashboard")

    df = pd.read_sql("SELECT * FROM inventory", conn)

    if df.empty:
        st.info("No stock available yet.")
        return

    total_skus = len(df)
    total_stock = df['stock'].sum()
    out_of_stock = (df['stock'] == 0).sum()
    categories = df['category'].value_counts()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total SKUs", total_skus)
    col2.metric("Total Stock Units", total_stock)
    col3.metric("Out of Stock", out_of_stock)

    st.write("### SKUs per Category")
    st.bar_chart(categories)

    quotes_df = pd.read_sql("SELECT * FROM quotes", conn)
    if not quotes_df.empty:
        quotes_df['month'] = pd.to_datetime(quotes_df['date']).dt.to_period('M')
        quotes_summary = quotes_df.groupby('month').size()
        st.write("### Quotes Generated per Month")
        st.line_chart(quotes_summary)

def show_stock():
    st.subheader("📦 View Stock")

    df = pd.read_sql("SELECT * FROM inventory", conn)
    if df.empty:
        st.info("No stock available.")
        return

    # Search / filter
    search = st.text_input("🔍 Search Products")
    if search:
        df = df[df.apply(lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1)]

    # Show table with headers
    st.dataframe(df[['sku', 'name', 'category', 'subcategory', 'price', 'stock', 'image_url']])

def add_stock():
    st.subheader("➕ Add Stock")

    with st.form("add_stock_form"):
        col1, col2 = st.columns(2)
        with col1:
            sku = st.text_input("SKU")
            name = st.text_input("Product Name")
            category = st.text_input("Category")
            subcategory = st.text_input("Subcategory")
        with col2:
            price = st.number_input("Price", min_value=0.0, format="%.2f")
            stock = st.number_input("Stock Quantity", min_value=0, step=1)
            image_url = st.text_input("Image URL")

        submitted = st.form_submit_button("Add Product")

        if submitted:
            c = conn.cursor()
            c.execute("INSERT INTO inventory (sku, name, category, subcategory, price, stock, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (sku, name, category, subcategory, price, stock, image_url))
            conn.commit()
            st.success(f"✅ {name} added successfully!")

def show_quotes():
    st.subheader("📝 Quotes")

    df = pd.read_sql("SELECT * FROM inventory", conn)
    if df.empty:
        st.info("No stock available to quote.")
        return

    # Multi-select for quote
    selected_skus = st.multiselect("Select Products for Quote", df['sku'])
    if selected_skus:
        selected_df = df[df['sku'].isin(selected_skus)].copy()
        selected_df['quote_qty'] = 0

        st.write("### Selected Products")
        st.dataframe(selected_df[['sku', 'name', 'price', 'stock']])

        customer_name = st.text_input("Customer Name")
        company = st.text_input("Company")
        address = st.text_area("Address")
        phone = st.text_input("Phone")

        if st.button("Generate Quote"):
            today = datetime.date.today().strftime("%Y-%m-%d")
            quote_number = pd.read_sql("SELECT COUNT(*) as cnt FROM quotes", conn)['cnt'][0] + 1
            grand_total = selected_df['price'].sum()

            c = conn.cursor()
            c.execute("""INSERT INTO quotes (quote_no, date, customer_name, company, address, phone, items, total)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                      (f"BG-{quote_number:04d}", today, customer_name, company, address, phone,
                       selected_df.to_json(orient="records"), grand_total))
            conn.commit()

            st.success(f"✅ Quote BG-{quote_number:04d} generated successfully!")

def user_management():
    st.subheader("👥 User Management")
    st.info("User management features can be expanded later.")

def logout():
    st.warning("You have been logged out.")

# ========== PAGE ROUTING ==========
if choice == "Dashboard":
    show_dashboard()
elif choice == "View Stock":
    show_stock()
elif choice == "Add Stock":
    add_stock()
elif choice == "Quotes":
    show_quotes()
elif choice == "User Management":
    user_management()
elif choice == "Logout":
    logout()
