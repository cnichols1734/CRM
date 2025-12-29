from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, SelectMultipleField, DecimalField, DateField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional, Regexp

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[DataRequired()])
    phone = StringField('Phone Number', validators=[Optional(), Length(max=20)])
    license_number = StringField(
        'License Number',
        validators=[
            Optional(),
            Length(max=16, message='License number must be 16 digits or fewer'),
            Regexp(r'^\d*$', message='License number must contain digits only')
        ]
    )
    licensed_supervisor = StringField('Licensed Supervisor of Associate', validators=[Optional(), Length(max=120)])
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
    
    # Client objective fields
    current_objective = TextAreaField('What does this person want right now?', 
                                    description='Ex: They want to buy a home, sell a home, they\'re looking for acreage, They are not looking to make a move right now, but I want to stay in touch with them.',
                                    validators=[Optional()])
    move_timeline = TextAreaField('What is their timeline?',
                                description='Ex: They want to move now, within the next 6 months, or within a year?',
                                validators=[Optional()])
    motivation = TextAreaField('Why do they want to move?',
                             description='Ex: They are having a baby, want a larger home, moving for work, etc.',
                             validators=[Optional()])
    financial_status = TextAreaField('Have they shared any financial details with you?',
                                   description='Ex: Their budget is $x, they have or have not been preapproved, they have $x for down payment, they have $x in home equity.',
                                   validators=[Optional()])
    additional_notes = TextAreaField('Any other details to share?',
                                   description='Ex: No details at this time',
                                   validators=[Optional()])
    
    submit = SubmitField('Save Contact')

class RequestResetForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password',
                                   validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')