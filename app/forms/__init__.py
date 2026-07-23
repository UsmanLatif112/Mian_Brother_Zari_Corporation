from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, Optional


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember me")
    submit = SubmitField("Sign In")


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    full_name = StringField("Full Name", validators=[DataRequired(), Length(max=120)])
    role = SelectField(
        "Role",
        choices=[
            ("admin", "Admin"),
            ("manager", "Manager"),
            ("sales", "Sales"),
            ("accountant", "Accountant"),
        ],
    )
    password = PasswordField("Password", validators=[Optional(), Length(min=6)])
    is_active_user = BooleanField("Active", default=True)
    submit = SubmitField("Save")


class ProductForm(FlaskForm):
    name = StringField("Product Name", validators=[DataRequired()])
    existing_product_id = HiddenField(
        "Existing Product",
        filters=[lambda v: None if v in (None, "", 0, "0") else v],
    )
    sku = StringField("SKU", validators=[DataRequired()])
    barcode = StringField("Barcode", validators=[Optional()])
    brand = StringField("Brand", validators=[Optional()])
    category_id = IntegerField("Category", validators=[DataRequired()])
    subcategory_id = IntegerField(
        "Subcategory",
        validators=[Optional()],
        filters=[lambda v: None if v in (None, "", 0, "0") else v],
    )
    purchase_price = DecimalField("Purchase Price", places=2)
    sale_price = DecimalField("Sale Price", places=2)
    wholesale_price = DecimalField("Wholesale Price", places=2)
    retail_price = DecimalField("Retail Price", places=2)
    tax_rate = DecimalField("GST/Tax %", places=2, default=0)
    opening_stock = DecimalField("Purchase Qty", places=3, default=0)
    minimum_stock = DecimalField("Minimum Stock", places=3, default=0)
    batch_number = StringField("Batch No.", validators=[Optional()])
    expiry_date = DateField("Expiry Date", validators=[Optional()], format="%Y-%m-%d")
    vendor_id = IntegerField("Vendor", validators=[DataRequired(message="Vendor is required.")])
    invoice_no = StringField("Invoice No.", validators=[Optional()])
    description = TextAreaField("Description", validators=[Optional()])
    submit = SubmitField("Save Product")


class CustomerForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    phone = StringField("Phone", validators=[Optional()])
    address = TextAreaField("Address", validators=[Optional()])
    old_book_no = StringField("Old Book No", validators=[Optional()])
    opening_balance = DecimalField(
        "Old Account Balance",
        places=2,
        validators=[Optional()],
        default=None,
    )
    joined_date = DateField("Date Added", validators=[Optional()])
    customer_type = SelectField(
        "Customer Type",
        choices=[
            ("good", "Good"),
            ("bad", "Bad"),
            ("1_year", "1 Year"),
            ("6_month", "6 Month"),
            ("late_pay", "Late Pay"),
        ],
        default="good",
    )
    cnic = StringField("CNIC", validators=[Optional()])
    credit_limit = DecimalField("Credit Limit", places=2, default=0)
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Save")


class VendorForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    phone = StringField("Phone", validators=[Optional()])
    address = TextAreaField("Address", validators=[Optional()])
    opening_balance = DecimalField(
        "Opening Balance",
        places=2,
        validators=[Optional()],
        default=None,
    )
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Save")


class ExpenseForm(FlaskForm):
    name = StringField("Expense Name", validators=[DataRequired()])
    description = TextAreaField("Description", validators=[Optional()])
    amount = DecimalField("Amount", places=2, validators=[DataRequired()])
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    expense_date = DateField("Date", validators=[DataRequired()])
    submit = SubmitField("Save")
