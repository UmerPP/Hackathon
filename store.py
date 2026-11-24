import sqlite3
from datetime import datetime, timezone
from flask import g, current_app

def db():
    if "database" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        g.database = conn
    return g.database

def rows(sql, *args):
    return db().execute(sql,args).fetchall()

def one(sql,*args):
    return db().execute(sql,args).fetchone()

def run(sql,*args):
    return db().execute(sql,args).lastrowid

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, role TEXT, dept TEXT, secret TEXT, last_step INTEGER DEFAULT -1, failures INTEGER DEFAULT 0, locked_until INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY, topic TEXT, place TEXT, dept TEXT, base INTEGER, private INTEGER, reason TEXT, status TEXT DEFAULT 'Open', cycle INTEGER DEFAULT 0, reviewed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS members(issue INTEGER REFERENCES issues, user INTEGER REFERENCES users, joined_at INTEGER DEFAULT (strftime('%s','now')), PRIMARY KEY(issue,user));
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, issue INTEGER REFERENCES issues, user INTEGER REFERENCES users, body TEXT);
CREATE TABLE IF NOT EXISTS electorate(issue INTEGER REFERENCES issues, cycle INTEGER, user INTEGER REFERENCES users, PRIMARY KEY(issue,cycle,user));
CREATE TABLE IF NOT EXISTS inspections(issue INTEGER REFERENCES issues, cycle INTEGER, PRIMARY KEY(issue,cycle));
CREATE TABLE IF NOT EXISTS votes(issue INTEGER REFERENCES issues, cycle INTEGER, user INTEGER REFERENCES users, choice TEXT, weight REAL, PRIMARY KEY(issue,cycle,user));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, issue INTEGER REFERENCES issues, stamp TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, user INTEGER REFERENCES users, delta INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
def event(issue, detail):
    run("INSERT INTO events(issue,stamp,detail) VALUES(?,?,?)",issue,now(),detail)

def trust(uid):
    return max(0,min(100,50+one("SELECT COALESCE(SUM(delta),0) total FROM ledger WHERE user=?",uid)["total"]))

def weight(uid):
    return min(1.5,max(.25,trust(uid)/50))

def members(i):
    return rows("SELECT user FROM members WHERE issue=?",i)

def priority(issue):
    if issue["base"] == 4:
        return 4
    ids = [r["user"] for r in members(issue["id"])]
    established = sum(weight(uid) for uid in ids if not fresh(uid))
    newcomers = sum(weight(uid) for uid in ids if fresh(uid))
    support = established + min(2, newcomers)
    return min(3,issue["base"] + int(len(ids)>=3 and support>=3) + int(len(ids)>=6 and support>=6))

def fresh(u):
    return not one("SELECT 1 FROM ledger WHERE user=? AND delta>0",u)

def initialize():
    import secrets
    db().executescript(SCHEMA)
    if "password_hash" not in [r["name"] for r in rows("PRAGMA table_info(users)")]:
        db().execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "email_verified" not in [r["name"] for r in rows("PRAGMA table_info(users)")]:
        db().execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
    if "approved" not in [r["name"] for r in rows("PRAGMA table_info(users)")]:
        db().execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 1")
    db().executescript("""
    CREATE TABLE IF NOT EXISTS drafts(token TEXT PRIMARY KEY,user INTEGER,body TEXT,result TEXT,expiry INTEGER);
    CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,issue INTEGER REFERENCES issues,dept TEXT,topic TEXT,place TEXT,created INTEGER,sent INTEGER,due INTEGER DEFAULT 0,state TEXT DEFAULT 'pending',note TEXT DEFAULT 'Waiting for delivery');
    CREATE INDEX IF NOT EXISTS alert_lookup ON alerts(dept,topic,place,created);
    """)
    db().execute("BEGIN IMMEDIATE")
    if not one("SELECT 1 FROM meta WHERE key='student_access_requires_approval'"):
        run("UPDATE users SET approved=0 WHERE role='Student'")
        run("INSERT INTO meta VALUES('student_access_requires_approval','1')")
    run("INSERT OR IGNORE INTO meta VALUES('generation',?)",secrets.token_hex(16))
    db().commit()
