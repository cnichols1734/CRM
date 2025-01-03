from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, SelectMultipleField, DecimalField, DateField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password',
                                   validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

class LoginForm(FlaskForm):
    username = StringField('Username or Email', validators=[
        DataRequired(message='Please enter your username or email')
    ])
    password = PasswordField('Password', validators=[
        DataRequired(message='Please enter your password')
    ])
    submit = SubmitField('Sign in')

class ContactForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[DataRequired()])
    email = StringField('Email', validators=[Optional(), Email()])
    phone = StringField('Phone')
    street_address = StringField('Street Address')
    city = StringField('City')
    state = StringField('State')
    zip_code = StringField('ZIP Code')
    notes = TextAreaField('Notes')
    potential_commission = DecimalField('Potential Commission', places=2, default=5000.00)
    group_ids = SelectMultipleField('Groups', coerce=int)
    
    # New date fields
    last_email_date = DateField('Last Email Date', validators=[Optional()])
    last_text_date = DateField('Last Text Date', validators=[Optional()])
    last_phone_call_date = DateField('Last Phone Call Date', validators=[Optional()])
    
    submit = SubmitField('Save Contact')

class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password',
                                   validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')