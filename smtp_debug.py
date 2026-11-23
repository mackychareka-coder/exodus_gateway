import smtplib
from config import load_config

c = load_config()
print("user:", c["SMTP_USER"], "| password length:", len(c["SMTP_PASSWORD"]))
s = smtplib.SMTP(c["SMTP_HOST"], c["SMTP_PORT"], timeout=15)
s.starttls()
try:
    s.login(c["SMTP_USER"], c["SMTP_PASSWORD"])
    print("LOGIN OK")
except Exception as e:
    print("LOGIN FAILED:", type(e).__name__, e)
try:
    s.quit()
except Exception:
    pass