from wtforms import Form, StringField, PasswordField, TextAreaField, RadioField, SubmitField
from wtforms.validators import DataRequired, Length

class LoginForm(Form):
    login = StringField("login", [DataRequired()])
    password = PasswordField("password", [DataRequired()])


class BroadcastForm(Form):
    audience = RadioField(
        "Получатели",
        choices=[("parents", "Родители"), ("children", "Дети")],
        default="parents",
        validators=[DataRequired()],
    )
    text = TextAreaField("Текст", validators=[DataRequired(), Length(min=1, max=4000)])
    submit = SubmitField("Отправить")