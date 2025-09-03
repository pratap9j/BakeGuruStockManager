import streamlit as st
import pandas as pd
import sqlite3
from PIL import Image
import io
import hashlib
from fpdf import FPDF
import requests
import datetime
import json

# --- Database Setup ---
conn = sqlite3.connect("inventory.db")
c = conn.cursor()

# Tables
c.execute('''CREATE TABLE IF NOT EXISTS products
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              sku TEXT,
              name TEXT,
              variation TEXT,
              category TEXT,
              subcategory TEXT,
              list_price REAL,
              wholesale_price REAL,
              retail_price REAL,
              qty INTEGER,
              image BLOB)''')

c.execute('''CREATE TABLE IF NOT EXISTS users
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE,
              password TEXT,
              role TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS company_settings
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT,
              address TEXT,
              phone TEXT,
              email TEXT,
              logo BLOB)''')

c.execute('''CREATE TABLE IF NOT EXISTS quote_counter
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              current_number INTEGER)''')

c.execute('''CREATE TABLE IF NOT EXISTS quotes
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              quote_no TEXT,
              date TEXT,
              customer_name TEXT,
              customer_company TEXT,
              customer_address TEXT,
              customer_phone TEXT,
              products TEXT,
              grand_total REAL)''')

conn.commit()

# Defaults
c.execute("SELECT * FROM users")
if not c.fetchone():
    c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
              ("admin", hashlib.sha256("admin123".encode()).hexdigest(), "Admin"))
    conn.commit()

c.execute("SELECT * FROM company_settings")
if not c.fetchone():
    c.execute("INSERT INTO company_settings (name, address, phone, email, logo) VALUES (?,?,?,?,?)",
              ("BakeGuru", "123 Bake Street, Bangalore, India", "+91-9876543210", "info@bakeguru.co.in", None))
    conn.commit()

c.execute("SELECT * FROM quote_counter")
if not c.fetchone():
    c.execute("INSERT INTO quote_counter (current_number) VALUES (0)")
    conn.commit()

# --- Helper Functions ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_user(username, password):
    c.execute("SELECT * FROM users WHERE username=? AND password=?",
              (username, hash_password(password)))
    return c.fetchone()

def create_user(username, password, role):
    try:
        c.execute("INSERT INTO users (username, password, role) VALUES (?,?,?)",
                  (username, hash_password(password), role))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def reset_password(username, new_password):
    c.execute("UPDATE users SET password=? WHERE username=?",
              (hash_password(new_password), username))
    conn.commit()

def load_products():
    return pd.read_sql_query("SELECT * FROM products", conn)

def add_product(sku, name, variation, category, subcategory,
                list_price, wholesale_price, retail_price, qty, image):
    c.execute("""INSERT INTO products
                 (sku, name, variation, category, subcategory,
                  list_price, wholesale_price, retail_price, qty, image)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (sku, name, variation, category, subcategory,
               list_price, wholesale_price, retail_price, qty, image))
    conn.commit()

def get_company_settings():
    c.execute("SELECT * FROM company_settings LIMIT 1")
    row = c.fetchone()
    return {
        "id": row[0],
        "name": row[1],
        "address": row[2],
        "phone": row[3],
        "email": row[4],
        "logo": row[5]
    }

def update_company_settings(name, address, phone, email, logo_data=None):
    if logo_data:
        c.execute("UPDATE company_settings SET name=?, address=?, phone=?, email=?, logo=? WHERE id=1",
                  (name, address, phone, email, logo_data))
    else:
        c.execute("UPDATE company_settings SET name=?, address=?, phone=?, email=? WHERE id=1",
                  (name, address, phone, email))
    conn.commit()

def get_next_quote_number():
    c.execute("SELECT current_number FROM quote_counter WHERE id=1")
    current = c.fetchone()[0]
    next_number = current + 1
    c.execute("UPDATE quote_counter SET current_number=? WHERE id=1", (next_number,))
    conn.commit()
    return next_number

def export_excel(df, filename="inventory.xlsx"):
    df.to_excel(filename, index=False)
    return filename

def export_quote_pdf(products, filename="quote.pdf", customer=None, quote_no=None, date=None):
    company = get_company_settings()
    pdf = FPDF()
    pdf.add_page()

    # Company logo
    if company and company["logo"]:
        logo_path = "company_logo.png"
        with open(logo_path, "wb") as f:
            f.write(company["logo"])
        try:
            pdf.image(logo_path, 10, 8, 30)
        except:
            pass

    # Company details
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(200, 10, company["name"], ln=True, align="C")
    pdf.set_font("Arial", '', 10)
    pdf.cell(200, 6, company["address"], ln=True, align="C")
    pdf.cell(200, 6, f"📞 {company['phone']} | ✉️ {company['email']}", ln=True, align="C")
    pdf.ln(10)

    # Quote info
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(100, 8, f"Quote No: {quote_no}", ln=0, align="L")
    pdf.cell(100, 8, f"Date: {date}", ln=1, align="R")
    pdf.ln(5)

    # Customer info
    if customer:
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(200, 8, "Quotation For:", ln=True, align="L")
        pdf.set_font("Arial", '', 10)
        if customer.get("name"): 
            pdf.cell(200, 6, f"Name: {customer['name']}", ln=True, align="L")
        if customer.get("company"): 
            pdf.cell(200, 6, f"Company: {customer['company']}", ln=True, align="L")
        if customer.get("address"): 
            pdf.multi_cell(200, 6, f"Address: {customer['address']}", align="L")
        if customer.get("phone"): 
            pdf.cell(200, 6, f"Phone: {customer['phone']}", ln=True, align="L")
        pdf.ln(5)

    # Table header
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 10, "SKU", 1)
    pdf.cell(60, 10, "Product Name", 1)
    pdf.cell(30, 10, "Quote Qty", 1)
    pdf.cell(30, 10, "Price", 1)
    pdf.cell(30, 10, "Total", 1)
    pdf.ln()

    # Table rows
    pdf.set_font("Arial", size=9)
    grand_total = 0
    for _, row in products.iterrows():
        line_total = row["quote_qty"] * row["retail_price"]
        grand_total += line_total
        pdf.cell(30, 10, str(row["sku"]), 1)
        pdf.cell(60, 10, str(row["name"]), 1)
        pdf.cell(30, 10, str(row["quote_qty"]), 1)
        pdf.cell(30, 10, f"{row['retail_price']:.2f}", 1)
        pdf.cell(30, 10, f"{line_total:.2f}", 1)
        pdf.ln()

    # Grand total
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(150, 10, "Grand Total", 1)
    pdf.cell(30, 10, f"{grand_total:.2f}", 1)
    pdf.ln(20)

    # Disclaimer
    pdf.set_font("Arial", 'I', 9)
    pdf.cell(200, 10, "Disclaimer: GST & Shipping Extra", ln=True, align="L")

    pdf.output(filename)
    return filename

# --- Streamlit App ---
st.set_page_config(page_title="BakeGuru Stock Manager", layout="wide")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.role = None
    st.session_state.username = None

# --- Login Page ---
if not st.session_state.logged_in:
    st.title("🔑 BakeGuru Stock Manager Login")
    username = st.text_input("Username", key="login_user")
    password = st.text_input("Password", type="password", key="login_pass")

    if st.button("Login"):
        user = verify_user(username, password)
        if user:
            st.session_state.logged_in = True
            st.session_state.username = user[1]
            st.session_state.role = user[3]
            st.success(f"✅ Welcome {st.session_state.username} ({st.session_state.role})")
            st.rerun()
        else:
            st.error("❌ Invalid username or password")

    st.info("Default login → Username: **admin** | Password: **admin123**")

# --- Main App ---
else:
    st.sidebar.write(f"👤 Logged in as **{st.session_state.username} ({st.session_state.role})**")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.role = None
        st.rerun()

    menu = ["Dashboard", "Analytics Dashboard", "View Stock"]
    if st.session_state.role in ["Admin", "Staff"]:
        menu.append("Add Product")
        menu.append("Bulk Upload")
        menu.append("Quotes History")
    if st.session_state.role == "Admin":
        menu.append("User Management")
        menu.append("Settings")
    choice = st.sidebar.selectbox("Menu", menu)

    # --- Dashboard (Stock) ---
    if choice == "Dashboard":
        df = load_products()
        total_products = len(df)
        total_qty = df["qty"].sum() if not df.empty else 0
        in_stock_products = df[df["qty"] > 0]["id"].nunique() if not df.empty else 0
        low_stock = df[df["qty"] < 5]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Products", total_products)
        col2.metric("In Stock (qty > 0)", in_stock_products)
        col3.metric("Total Stock Qty", total_qty)

        if not low_stock.empty:
            st.warning(f"⚠️ {len(low_stock)} products are low in stock!")
            st.table(low_stock[["sku", "name", "qty"]])

    # --- Analytics Dashboard ---
    elif choice == "Analytics Dashboard":
        st.title("📊 Business Analytics Dashboard")
        df = load_products()
        if not df.empty:
            total_skus = len(df)
            in_stock = len(df[df["qty"] > 0])
            out_stock = len(df[df["qty"] <= 0])

            col1, col2, col3 = st.columns(3)
            col1.metric("Total SKUs", total_skus)
            col2.metric("In Stock SKUs", in_stock)
            col3.metric("Out of Stock SKUs", out_stock)

            cat_counts = df["category"].value_counts().reset_index()
            cat_counts.columns = ["Category", "Count"]
            st.bar_chart(cat_counts.set_index("Category"))

            subcat_counts = df["subcategory"].value_counts().reset_index()
            subcat_counts.columns = ["Subcategory", "Count"]
            st.bar_chart(subcat_counts.set_index("Subcategory"))
        else:
            st.info("No product data available yet.")

        st.subheader("📝 Quotes Insights")
        quotes_df = pd.read_sql_query("SELECT * FROM quotes ORDER BY date", conn)
        if not quotes_df.empty:
            total_quotes = len(quotes_df)
            total_value = quotes_df["grand_total"].sum()

            col1, col2 = st.columns(2)
            col1.metric("Total Quotes", total_quotes)
            col2.metric("Total Quoted Value (₹)", f"{total_value:,.2f}")

            quotes_df["date"] = pd.to_datetime(quotes_df["date"], format="%d-%m-%Y")
            monthly = quotes_df.groupby(quotes_df["date"].dt.to_period("M")).agg({
                "id": "count",
                "grand_total": "sum"
            }).reset_index()
            monthly["date"] = monthly["date"].astype(str)

            st.line_chart(monthly.set_index("date")[["id"]])
            st.bar_chart(monthly.set_index("date")[["grand_total"]])

            top_customers = quotes_df.groupby("customer_company").size().sort_values(ascending=False).head(5)
            st.subheader("🏆 Top 5 Customers (by Quotes Count)")
            st.bar_chart(top_customers)
        else:
            st.info("No quotes data available yet.")

    # --- Add Product ---
    elif choice == "Add Product":
        st.subheader("➕ Add a New Product")
        sku = st.text_input("Product SKU", key="add_sku")
        name = st.text_input("Product Name", key="add_name")
        variation = st.text_input("Variation", key="add_variation")
        category = st.text_input("Category", key="add_category")
        subcategory = st.text_input("Sub Category", key="add_subcategory")
        list_price = st.number_input("List Price", 0.0, key="add_list_price")
        wholesale_price = st.number_input("Wholesale Price", 0.0, key="add_wholesale_price")
        retail_price = st.number_input("Retail Price", 0.0, key="add_retail_price")
        qty = st.number_input("Quantity", 0, key="add_qty")
        image_file = st.file_uploader("Upload Image", type=["jpg", "png"], key="add_image")

        if st.button("Add Product", key="btn_add_product"):
            image_data = None
            if image_file is not None:
                buf = io.BytesIO()
                Image.open(image_file).save(buf, format="PNG")
                image_data = buf.getvalue()
            add_product(sku, name, variation, category, subcategory,
                        list_price, wholesale_price, retail_price, qty, image_data)
            st.success(f"✅ {name} added!")

    # --- Bulk Upload ---
    elif choice == "Bulk Upload":
        st.subheader("📥 Bulk Upload Products")
        st.markdown("""
        **Template Columns Required:**  
        - sku, name, variation, category, subcategory, list_price, wholesale_price, retail_price, qty, image_url  
        """)
        uploaded_file = st.file_uploader("Upload Excel or CSV", type=["xlsx", "csv"], key="bulk_upload")

        if uploaded_file:
            try:
                if uploaded_file.name.endswith(".csv"):
                    df_upload = pd.read_csv(uploaded_file)
                else:
                    df_upload = pd.read_excel(uploaded_file)

                st.write("Preview of uploaded file:")
                st.dataframe(df_upload.head())

                if st.button("Import Products", key="btn_import"):
                    imported_count = 0
                    for _, row in df_upload.iterrows():
                        image_data = None
                        if pd.notna(row.get("image_url", "")):
                            try:
                                resp = requests.get(row["image_url"], timeout=10)
                                if resp.status_code == 200:
                                    image_data = resp.content
                            except Exception as e:
                                st.warning(f"⚠️ Could not fetch image for {row['sku']} ({row['name']}): {e}")

                        add_product(
                            str(row["sku"]),
                            str(row["name"]),
                            str(row.get("variation", "")),
                            str(row.get("category", "")),
                            str(row.get("subcategory", "")),
                            float(row.get("list_price", 0)),
                            float(row.get("wholesale_price", 0)),
                            float(row.get("retail_price", 0)),
                            int(row.get("qty", 0)),
                            image_data
                        )
                        imported_count += 1
                    st.success(f"✅ {imported_count} products imported successfully!")
            except Exception as e:
                st.error(f"❌ Error reading file: {e}")

    # --- View Stock & Quote Builder ---
    elif choice == "View Stock":
        df = load_products()
        if not df.empty:
            st.subheader("📋 Inventory List")

            category_filter = st.selectbox("Filter by Category", ["All"] + sorted(df["category"].dropna().unique().tolist()), key="stock_category")
            search_text = st.text_input("Search by SKU or Name", key="stock_search")

            if category_filter != "All":
                df = df[df["category"] == category_filter]
            if search_text:
                df = df[df.apply(lambda row: search_text.lower() in str(row["sku"]).lower() or search_text.lower() in str(row["name"]).lower(), axis=1)]

            st.write("Select products for your quotation:")
            selected_products = []
            for i, row in df.iterrows():
                cols = st.columns([0.5, 2, 2, 2, 1])
                with cols[0]:
                    if st.checkbox("", key=f"chk_{row['id']}"):
                        selected_products.append(row["id"])
                with cols[1]:
                    st.text(row["sku"])
                with cols[2]:
                    st.text(row["name"])
                with cols[3]:
                    st.text(f"₹{row['retail_price']}")
                with cols[4]:
                    st.text(f"{row['qty']}")

            if selected_products:
                if st.button("➡️ Generate Quote", key="btn_generate_quote"):
                    st.session_state["quote_ids"] = selected_products
                    st.session_state["step"] = "quote"
                    st.rerun()
        else:
            st.info("No products added yet.")

    # --- Generate Quote Step ---
    elif "step" in st.session_state and st.session_state["step"] == "quote":
        st.subheader("📝 Generate Customer Quote")

        df = load_products()
        selected_df = df[df["id"].isin(st.session_state["quote_ids"])].copy()

        # Customer Details
        st.subheader("👤 Customer Details (Optional)")
        customer_name = st.text_input("Customer Name", "", key="cust_name")
        customer_company = st.text_input("Customer Company", "", key="cust_company")
        customer_address = st.text_area("Customer Address", "", key="cust_address")
        customer_phone = st.text_input("Customer Phone", "", key="cust_phone")

        customer_info = None
        if customer_name or customer_company or customer_address or customer_phone:
            customer_info = {
                "name": customer_name,
                "company": customer_company,
                "address": customer_address,
                "phone": customer_phone
            }

        # Editable Quote Qty & Price
        for i, row in selected_df.iterrows():
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            with col1:
                st.text(f"{row['sku']} - {row['name']}")
            with col2:
                st.text(f"Available: {row['qty']}")
            with col3:
                selected_df.loc[i, "quote_qty"] = st.number_input(f"Quote Qty {row['id']}", min_value=1, value=1, key=f"quote_qty_{row['id']}")
            with col4:
                selected_df.loc[i, "retail_price"] = st.number_input(f"Price {row['id']}", min_value=0.0, value=float(row["retail_price"]), key=f"price_{row['id']}")

        # Export Quote
        if st.button("⬇️ Save & Export Quote", key="btn_export_quote"):
            quote_number = get_next_quote_number()
            today = datetime.date.today().strftime("%d-%m-%Y")
            grand_total = (selected_df["quote_qty"] * selected_df["retail_price"]).sum()

            # Save in DB
            c.execute("""INSERT INTO quotes
                         (quote_no, date, customer_name, customer_company, customer_address, customer_phone, products, grand_total)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                      (f"BG-{quote_number:04d}", today, customer_name, customer_company, customer_address, customer_phone,
                       selected_df.to_json(orient="records"), grand_total))
            conn.commit()

            file = export_quote_pdf(selected_df, "quote.pdf",
                                    customer=customer_info,
                                    quote_no=f"BG-{quote_number:04d}",
                                    date=today)
            with open(file, "rb") as f:
                st.download_button("Download Quote (PDF)", f, file_name=f"BG-{quote_number:04d}.pdf")

    # --- Quotes History ---
    elif choice == "Quotes History":
        st.subheader("📜 Past Quotes")
        quotes_df = pd.read_sql_query("SELECT * FROM quotes ORDER BY id DESC", conn)

        if not quotes_df.empty:
            st.dataframe(quotes_df[["quote_no", "date", "customer_name", "customer_company", "grand_total"]])

            selected_quote = st.selectbox("Select Quote to View", quotes_df["quote_no"].tolist(), key="select_quote")
            if selected_quote:
                q = quotes_df[quotes_df["quote_no"] == selected_quote].iloc[0]
                st.write(f"**Customer:** {q['customer_name']} ({q['customer_company']})")
                st.write(f"**Date:** {q['date']}")
                st.write(f"**Total:** ₹{q['grand_total']}")

                products = pd.read_json(q["products"])
                st.table(products[["sku", "name", "quote_qty", "retail_price"]])

                if st.button("⬇️ Download PDF Again", key="btn_redownload_pdf"):
                    customer_info = {
                        "name": q["customer_name"],
                        "company": q["customer_company"],
                        "address": q["customer_address"],
                        "phone": q["customer_phone"]
                    }
                    file = export_quote_pdf(products, "quote.pdf",
                                            customer=customer_info,
                                            quote_no=q["quote_no"],
                                            date=q["date"])
                    with open(file, "rb") as f:
                        st.download_button("Download Quote (PDF)", f, file_name=f"{q['quote_no']}.pdf")
        else:
            st.info("No quotes generated yet.")

    # --- User Management ---
    elif choice == "User Management":
        st.subheader("👥 User Management")

        # Add User
        new_user = st.text_input("New Username", key="um_new_user")
        new_pass = st.text_input("New Password", type="password", key="um_new_pass")
        role = st.selectbox("Role", ["Admin", "Staff", "Viewer"], key="um_role")

        if st.button("Add User", key="btn_add_user"):
            if create_user(new_user, new_pass, role):
                st.success(f"✅ User {new_user} created with role {role}")
            else:
                st.error("❌ Username already exists")

        st.divider()

        # Reset Password
        reset_user = st.text_input("Reset Password for User", key="um_reset_user")
        reset_new_pass = st.text_input("New Password", type="password", key="um_reset_pass")

        if st.button("Reset Password", key="btn_reset_pass"):
            reset_password(reset_user, reset_new_pass)
            st.success("✅ Password reset")

    # --- Settings ---
    elif choice == "Settings" and st.session_state.role == "Admin":
        st.subheader("⚙️ Company Settings")
        settings = get_company_settings()

        name = st.text_input("Company Name", settings["name"], key="settings_name")
        address = st.text_area("Address", settings["address"], key="settings_address")
        phone = st.text_input("Phone", settings["phone"], key="settings_phone")
        email = st.text_input("Email", settings["email"], key="settings_email")

        logo_file = st.file_uploader("Upload Logo", type=["png", "jpg", "jpeg"], key="settings_logo")
        logo_data = None
        if logo_file:
            logo_data = logo_file.read()
            st.image(logo_data, width=150)

        if st.button("Save Settings", key="btn_save_settings"):
            update_company_settings(name, address, phone, email, logo_data)
            st.success("✅ Company settings updated")
