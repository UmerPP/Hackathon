import hmac
import io
import re
import secrets
from smtplib import SMTPAuthenticationError
import time

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
import pyotp
import qrcode
from qrcode.image.svg import SvgPathImage
from werkzeug.security import check_password_hash, generate_password_hash
from store import db, one, run
from triage import DEPTS
from integrations import send_verification

auth=Blueprint("auth",__name__)
ROLES={"student":"Student","staff":"Staff","admin":"Admin"}

@auth.get("/login")
def choose():
    return render_template("portals.html")

def match_step(secret,last,code):
    if not (code.isascii() and code.isdigit() and len(code)==6):
        return None
    step=int(time.time())//30
    return next((s for s in (step,step-1,step+1) if s>last and hmac.compare_digest(pyotp.TOTP(secret).at(s*30),code)),None)

def sign_in(account, setup=False):
    db().commit()
    session.clear()
    session.update(
        uid=account["id"], role=account["role"], setup=setup,
        generation=one("SELECT value FROM meta WHERE key='generation'")["value"],
    )
    if setup:
        return redirect(url_for("auth.setup"))
    flash("Signed in.")
    return redirect(url_for("home"))


def failed_attempt(account):
    run("""UPDATE users SET failures=(failures+1)%5,
        locked_until=CASE WHEN failures>=4 THEN ? ELSE 0 END WHERE id=?""",
        int(time.time())+60, account["id"])


@auth.route("/login/<portal>",methods=["GET","POST"])
def login(portal):
    if portal not in ROLES:
        abort(404)
    role=ROLES[portal]
    challenge=session.get("challenge",{})
    awaiting_code=challenge.get("role")==role and challenge.get("until",0)>time.time()

    def feedback(message):
        db().commit()
        flash(message)
        return redirect(url_for("auth.login",portal=portal))

    if request.method=="GET":
        if g.user:
            return redirect(url_for("home"))
        return render_template("login.html",portal=portal,role=role,awaiting_code=awaiting_code)
    if g.user:
        return redirect(url_for("home"))

    action=request.form.get("action")
    name=request.form.get("name","").strip().casefold()
    password=request.form.get("password","")
    if action=="cancel":
        session.pop("challenge",None)
        return redirect(url_for("auth.login",portal=portal))
    if action=="register":
        session.pop("challenge",None)
        if len(name)>254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+",name) or not 10<=len(password)<=128:
            return feedback("Enter your email address and a password of 10–128 characters.")
        if one("SELECT id FROM users WHERE name=?",name):
            return feedback("That account name is already registered.")
        run("INSERT INTO users(name,role,password_hash,approved) VALUES(?,?,?,0)",
            name,role,generate_password_hash(password))
        return feedback("Account created. Sign in with your email and password.")

    if action=="signin":
        session.pop("challenge",None)
        account=one("SELECT * FROM users WHERE name=? AND role=?",name,role)
        if not account or not account["password_hash"] or account["locked_until"]>time.time():
            return feedback("Sign-in failed or temporarily locked.")
        if not check_password_hash(account["password_hash"],password):
            failed_attempt(account)
            return feedback("Sign-in failed.")
        if not account["secret"]:
            run("INSERT OR IGNORE INTO meta VALUES(?,?)",
                setup_key(account),pyotp.random_base32())
            run("UPDATE users SET failures=0,locked_until=0 WHERE id=?",account["id"])
            return sign_in(account,setup=True)
        session["challenge"]={"uid":account["id"],"role":role,"until":int(time.time())+300}
        return redirect(url_for("auth.login",portal=portal))

    if action=="otp":
        if not awaiting_code:
            return feedback("Sign in with your password again.")
        account=one("SELECT * FROM users WHERE id=? AND role=?",challenge["uid"],role)
        if not account or not account["secret"] or account["locked_until"]>time.time():
            session.pop("challenge",None)
            return feedback("Sign-in failed or temporarily locked.")
        step=match_step(account["secret"],account["last_step"],request.form.get("code","").strip())
        if step is None:
            failed_attempt(account)
            if account["failures"]>=4:
                session.pop("challenge",None)
            return feedback("Code invalid, expired, or already used.")
        run("UPDATE users SET last_step=?,failures=0,locked_until=0 WHERE id=?",step,account["id"])
        return sign_in(account)
    abort(400)


def setup_key(account):
    return f"authenticator-setup:{account['id']}"


@auth.route("/authenticator/setup",methods=["GET","POST"])
def setup():
    if not session.get("setup"):
        return redirect(url_for("home"))
    if request.method=="POST":
        pending=one("SELECT value FROM meta WHERE key=?",setup_key(g.user))
        if not g.user["secret"] and pending:
            run("UPDATE users SET secret=? WHERE id=?",pending["value"],g.user["id"])
            run("DELETE FROM meta WHERE key=?",setup_key(g.user))
            db().commit()
            session.pop("setup",None)
            flash("Authenticator enabled for your next sign-in.")
            return redirect(url_for("home"))
        session.clear()
        return redirect(url_for("auth.choose"))
    return render_template("authenticator_setup.html")


@auth.get("/authenticator/setup.svg")
def qr():
    if not g.user or not session.get("setup") or g.user["secret"]:
        abort(404)
    pending=one("SELECT value FROM meta WHERE key=?",setup_key(g.user))
    if not pending:
        abort(404)
    uri=pyotp.TOTP(pending["value"]).provisioning_uri(name=g.user["name"],issuer_name="ReportFix")
    buf=io.BytesIO()
    qrcode.make(uri,image_factory=SvgPathImage).save(buf)
    return current_app.response_class(buf.getvalue(),mimetype="image/svg+xml")

@auth.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.choose"))

@auth.get("/access-pending")
def access_pending():
    if g.user["approved"]:
        return redirect(url_for("home"))
    return render_template("access_pending.html")


@auth.post("/account-access/<int:account_id>")
def approve_account(account_id):
    if g.user["role"]!="Admin" or not g.user["approved"]:
        abort(403)
    account=one("SELECT role FROM users WHERE id=? AND approved=0",account_id)
    if not account or account["role"] not in ("Student","Staff"):
        abort(404)
    department=None
    if account["role"]=="Staff":
        department=request.form.get("dept")
        if department not in DEPTS:
            abort(400)
    run("UPDATE users SET approved=1,dept=? WHERE id=?",department,account_id)
    if department:
        run("UPDATE alerts SET due=0 WHERE dept=? AND state='pending' AND note='Waiting for an approved department account'",department)
    db().commit()
    flash(f"{account['role']} access approved.")
    return redirect(url_for("home"))


@auth.post("/verify-email")
def verify_email():
    user=g.user
    def back(message):
        db().commit()
        flash(message)
        return redirect(url_for("home"))
    if user["email_verified"]:
        return back("This email is already verified.")
    if "@" not in user["name"]:
        return back("This legacy account has no email address. Register with an email address.")
    key=f"email-verify:{user['id']}"
    stored=one("SELECT value FROM meta WHERE key=?",key)
    pending=stored["value"].split() if stored else None
    now=int(time.time())
    if request.form.get("action")=="send":
        if pending and int(pending[0])>now-60:
            return back("Wait one minute before requesting another code.")
        code=f"{secrets.randbelow(1000000):06d}"
        try:
            sent=send_verification(user["name"],code)
        except SMTPAuthenticationError:
            if user["role"]=="Admin":
                return back("Gmail rejected the sender credentials. Update the Gmail app password in usersecrets.json, then retry.")
            return back("Gmail rejected the sender credentials. Ask the registrar to update the email settings.")
        if not sent:
            return back("ReportFix could not send the verification email. Check the Gmail configuration and connection.")
        run("INSERT OR REPLACE INTO meta VALUES(?,?)",key,f"{now} 0 {generate_password_hash(code)}")
        return back("Verification code sent. Check your email.")
    if not pending or int(pending[0])+600<=now or int(pending[1])>=5:
        return back("Request a new verification code.")
    if not check_password_hash(pending[2],request.form.get("code","")):
        run("UPDATE meta SET value=? WHERE key=?",f"{pending[0]} {int(pending[1])+1} {pending[2]}",key)
        return back("Incorrect email verification code.")
    run("DELETE FROM meta WHERE key=?",key)
    run("UPDATE users SET email_verified=1 WHERE id=?",user["id"])
    return back("Email verified.")
