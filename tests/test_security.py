import pytest
from flask import Flask

from app.utils.security import generate_otp, otp_matches
from config import Config


def test_generate_otp_is_six_digit_value():
    otp = generate_otp()

    assert otp.isdigit()
    assert len(otp) == 6


def test_otp_matches_requires_same_value():
    assert otp_matches('123456', '123456') is True
    assert otp_matches('123456', '654321') is False
    assert otp_matches(None, '123456') is False


@pytest.mark.parametrize('secret_key', [None, 'secret', 'too-short'])
def test_production_rejects_missing_or_weak_secret_key(secret_key):
    app = Flask(__name__)
    app.config.update(APP_ENV='production', SECRET_KEY=secret_key)

    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        Config.validate_security_configuration(app)


def test_development_missing_secret_key_gets_ephemeral_secure_value():
    app = Flask(__name__)
    app.config.update(APP_ENV='development', SECRET_KEY=None)

    Config.validate_security_configuration(app)

    assert len(app.config['SECRET_KEY']) >= 32
