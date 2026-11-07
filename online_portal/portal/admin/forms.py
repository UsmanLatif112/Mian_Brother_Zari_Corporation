from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class CompanyForm(FlaskForm):
    name = StringField("Company name", validators=[DataRequired(), Length(max=200)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])
    is_active = BooleanField("Active", default=True)
    logo = FileField(
        "Company / app logo",
        validators=[Optional(), FileAllowed(["jpg", "jpeg", "png", "webp", "gif"], "Images only")],
    )
    submit = SubmitField("Save company & logo")


class CompanyUserForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=120)])
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[Optional(), Email(), Length(max=120)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6, max=128)])
    role = SelectField(
        "Role",
        choices=[
            ("company_admin", "Company Admin (online view)"),
            ("shop_clerk", "Shop Clerk (offline add + view)"),
        ],
        validators=[DataRequired()],
    )
    subscription_plan = SelectField(
        "After key (paid plan)",
        choices=[
            ("lifetime", "Lifetime"),
            ("1_month", "1 Month"),
            ("1_year", "1 Year"),
        ],
        default="lifetime",
        validators=[DataRequired()],
    )
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Create user")
