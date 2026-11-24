import argparse
import hmac
import json
import os
import secrets
from accounts import auth
from integrations import categorize, queue_alert, start_delivery
import time

from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

from store import db, event, fresh, initialize, members, one, priority, rows, run, trust, weight
from triage import DEPTS, PLACES, PRIORITIES, TOPICS

ROOT = Path(__file__).resolve().parent

def create_app(data_dir=None):
    app = Flask(__name__)
    data = Path(data_dir or os.getenv("REPORTFIX_DATA_DIR") or ROOT / "instance")
    data.mkdir(exist_ok=True, parents=True)
    key = data / "session.key"
    if not key.exists():
        with key.open("xb") as out:
            out.write(secrets.token_bytes(48))
    app.config.update(SECRET_KEY=key.read_bytes(), DATABASE=data / "campus.db", SETTINGS=ROOT/"usersecrets.json",
                      SESSION_COOKIE_NAME="reportfix_campus",
                      SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=16000)
    with app.app_context():
        initialize()
        db().close()

    app.register_blueprint(auth)

    @app.teardown_appcontext
    def close(_):
        if "database" in g:
            g.database.close()

    @app.before_request
    def context():
        if request.endpoint == "static":
            return
        if request.method == "POST":
            token=request.form.get("csrf","")
            if not token.isascii() or not hmac.compare_digest(token,session.get("csrf") or "!missing"):
                abort(403)
            if request.endpoint!="report" or request.form.get("draft"):
                db().execute("BEGIN IMMEDIATE")
        generation = one("SELECT value FROM meta WHERE key='generation'")["value"]
        if session.get("generation") != generation:
            if request.method=="POST":
                abort(409)
            session.clear()
            session["generation"] = generation
        session.setdefault("csrf",secrets.token_urlsafe(24))
        g.user = one("SELECT * FROM users WHERE id=?",session.get("uid",-1))
        if g.user and session.get("role")!=g.user["role"]:
            g.user=None
        if request.endpoint not in ("auth.choose","auth.login","auth.qr","static") and not g.user:
            return redirect(url_for("auth.choose"))
        if g.user and session.get("setup"):
            if g.user["secret"]:
                # Another first-sign-in session finished enrollment.
                session.clear()
                return redirect(url_for("auth.choose"))
            if request.endpoint not in ("auth.setup","auth.qr","auth.logout"):
                return redirect(url_for("auth.setup"))
        if g.user and not g.user["approved"] and request.endpoint not in (
            "auth.access_pending","auth.setup","auth.qr","auth.logout"
        ):
            return redirect(url_for("auth.access_pending"))

    @app.after_request
    def protect(response):
        response.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                                 "Referrer-Policy":"no-referrer","X-Frame-Options":"DENY"})
        return response

    def finish(message, endpoint="home", **kw):
        db().commit()
        flash(message)
        return redirect(url_for(endpoint,**kw))

    def role(*allowed):
        if g.user["role"] not in allowed:
            abort(403)

    def member(i):
        return one("SELECT 1 FROM members WHERE issue=? AND user=?",i,g.user["id"])

    def visible(i):
        issue = one("SELECT * FROM issues WHERE id=?",i)
        if not issue:
            abort(404)
        u = g.user
        if u["role"]=="Admin":
            return issue
        if u["role"]=="Staff" and (issue["private"] or u["dept"]!=issue["dept"]):
            abort(404)
        if u["role"]=="Student" and issue["private"] and not member(i):
            abort(404)
        return issue

    @app.context_processor
    def common():
        return dict(departments=DEPTS, user=g.get("user"), csrf=session.get("csrf"), places=PLACES,
                    priorities=PRIORITIES, trust=trust, priority=priority, topics=TOPICS)

    @app.get("/")
    def home():
        u=g.user
        scope=request.args.get("view","board") if u["role"]=="Student" else "board"
        if scope not in ("board","mine","trending"):
            scope="board"
        where, args = "", []
        if u["role"]=="Staff":
            where,args="WHERE dept=? AND private=0",[u["dept"]]
        elif u["role"]=="Student":
            owned="id IN (SELECT issue FROM members WHERE user=?)"
            where,args=("WHERE "+owned if scope=="mine" else "WHERE private=0"),([u["id"]] if scope=="mine" else [])
        listing=rows("SELECT * FROM issues "+where,*args)
        listing=sorted(listing,key=lambda x:(x["status"] in ("Verified","Confirmed","Dismissed"),-priority(x),-len(members(x["id"])),-x["id"]))
        if scope=="trending":
            recent=rows("SELECT issue,user FROM members WHERE joined_at>=?",int(time.time())-86400)
            momentum={}
            for m in recent:
                momentum[m["issue"]]=momentum.get(m["issue"],0)+weight(m["user"])
            listing=[x for x in listing if not x["private"] and x["status"] in ("Open","In progress","Reopened") and x["id"] in momentum]
            listing.sort(key=lambda x:momentum[x["id"]],reverse=True)
        return render_template("home.html",listing=listing,scope=scope,members=members,
                               pending_accounts=rows("SELECT id,name,role FROM users WHERE role IN ('Student','Staff') AND approved=0 ORDER BY id") if u["role"]=="Admin" else [],
                               alerts=rows("SELECT * FROM alerts ORDER BY id DESC LIMIT 30") if u["role"]=="Admin" else [],
                               ledger=rows("SELECT * FROM ledger WHERE user=? ORDER BY id DESC",u["id"]))

    @app.post("/report")
    def report():
        role("Student")
        uid=g.user["id"]
        token=request.form.get("draft","")
        if not token:
            body=request.form.get("body","").strip()
            if not 4<=len(body)<=1200:
                return finish("Describe the problem in 4–1,200 characters.")
            d,source=categorize(body,request.form.get("private")=="yes")
            selected=request.form.get("dept")
            if not d["private"] and selected in DEPTS:
                d["dept"]=selected
            token=secrets.token_urlsafe(32)
            run("DELETE FROM drafts WHERE expiry<=?",int(time.time()))
            run("INSERT INTO drafts VALUES(?,?,?,?,?)",token,uid,"" if d["private"] else body,json.dumps(d),int(time.time())+900)
            db().commit()
            return render_template("preview.html",d=d,body=body,source=source,draft=token)
        draft=one("SELECT * FROM drafts WHERE token=? AND user=? AND expiry>?",token,uid,int(time.time()))
        if not draft:
            return finish("This review expired or was already submitted.")
        d=json.loads(draft["result"])
        body=draft["body"]
        place=request.form.get("place","")
        if place not in PLACES:
            abort(400)
        d["place"]=place
        if not d["private"]:
            selected=request.form.get("dept")
            if selected not in DEPTS and not (selected=="Admin" and d["dept"]=="Admin"):
                abort(400)
            d["dept"]=selected
            if d["base"]!=4:
                topic=request.form.get("topic") or d["topic"]
                if topic not in TOPICS:
                    abort(400)
                d.update(topic=topic,base=TOPICS[topic][1])
            d["reason"]=("Safety hazard. " if d["base"]==4 else "")+f"{d['topic']} at {place}. Student confirmed {selected} as the responsible department."
        run("DELETE FROM drafts WHERE token=?",token)
        issue=None if d["private"] else one("SELECT * FROM issues WHERE topic=? AND place=? AND dept=? AND private=0 AND status IN ('Open','In progress','Reopened','Awaiting votes')",d["topic"],place,d["dept"])
        uid=g.user["id"]
        if issue:
            i=issue["id"]
            if member(i):
                queue_alert(issue)
                return finish("You already count once on this issue.","detail",i=i)
        else:
            i=db().execute("INSERT INTO issues(topic,place,dept,base,private,reason) VALUES(:topic,:place,:dept,:base,:private,:reason)",d).lastrowid
        run("INSERT INTO members(issue,user) VALUES(?,?)",i,uid)
        if not d["private"]:
            run("INSERT INTO reports(issue,user,body) VALUES(?,?,?)",i,uid,body)
        event(i,"Another affected student reported the same problem." if issue else "Report received and routed to "+d["dept"]+".")
        queue_alert(one("SELECT * FROM issues WHERE id=?",i))
        return finish("Report recorded.","detail",i=i)

    @app.get("/issues/<int:i>")
    def detail(i):
        issue=visible(i)
        eligible=one("SELECT 1 FROM electorate WHERE issue=? AND cycle=? AND user=?",i,issue["cycle"],g.user["id"])
        return render_template("detail.html",x=issue,joined=member(i),eligible=eligible,count=len(members(i)),
            history=rows("SELECT * FROM events WHERE issue=? ORDER BY id DESC",i),
            inspected=one("SELECT 1 FROM inspections WHERE issue=? AND cycle=?",i,issue["cycle"]),
            vote=one("SELECT * FROM votes WHERE issue=? AND user=? AND cycle=?",i,g.user["id"],issue["cycle"]),
            ballots=rows("SELECT choice,count(*) n,round(sum(weight),2) w FROM votes WHERE issue=? AND cycle=? GROUP BY choice",i,issue["cycle"]),
            originals=rows("SELECT body FROM reports WHERE issue=?",i) if not issue["private"] and g.user["role"]!="Student" else [])

    @app.post("/issues/<int:i>/<action>")
    def act(i,action):
        x=visible(i)
        uid=g.user["id"]
        status=x["status"]
        opened=status in ("Open","In progress","Reopened")
        if action=="join":
            role("Student")
            if x["private"] or not opened:
                abort(403)
            if not member(i):
                run("INSERT INTO members(issue,user) VALUES(?,?)",i,uid)
                event(i,"Another student joined.")
        elif action in ("start","resolve"):
            role("Admin","Staff")
            if not opened:
                abort(409)
            if action=="start":
                if status=="In progress":
                    abort(409)
                run("UPDATE issues SET status='In progress' WHERE id=?",i)
                event(i,"Department started work.")
            else:
                run("UPDATE issues SET status='Awaiting votes',cycle=cycle+1 WHERE id=?",i)
                run("INSERT INTO electorate SELECT issue,?,user FROM members WHERE issue=?",x["cycle"]+1,i)
                event(i,"Staff marked the fix resolved. Affected students can now vote.")
        elif action in ("confirm","dispute"):
            role("Student")
            eligible=one("SELECT 1 FROM electorate WHERE issue=? AND cycle=? AND user=?",i,x["cycle"],uid)
            if not eligible or status not in ("Awaiting votes","Confirmed"):
                abort(403)
            if one("SELECT 1 FROM votes WHERE issue=? AND cycle=? AND user=?",i,x["cycle"],uid):
                abort(409)
            run("INSERT INTO votes VALUES(?,?,?,?,?)",i,x["cycle"],uid,action,weight(uid))
            votes=rows("SELECT * FROM votes WHERE issue=? AND cycle=?",i,x["cycle"])
            dissent=[v for v in votes if v["choice"]=="dispute"]
            established=sum(v["weight"] for v in dissent if not fresh(v["user"]))
            newcomer=sum(v["weight"] for v in dissent if fresh(v["user"]))
            event(i,"Affected student "+("confirmed" if action=="confirm" else "disputed")+" the fix.")
            if len(dissent)>=2 and established+min(1,newcomer)>=2:
                run("UPDATE issues SET status='Reopened' WHERE id=?",i)
                event(i,"Two or more disputes reached the credibility threshold. Queue reopened.")
            elif len(votes)==one("SELECT count(*) n FROM electorate WHERE issue=? AND cycle=?",i,x["cycle"])["n"] and not dissent:
                run("UPDATE issues SET status='Confirmed' WHERE id=?",i)
                event(i,"Every affected student confirmed the fix.")
            elif len(dissent)>=2 and newcomer>1:
                event(i,"New-account voting cluster flagged for registrar review.")
        elif action in ("verified","false","failed"):
            role("Admin")
            if x["reviewed"] or (action!="false" and status not in ("Awaiting votes","Confirmed","Reopened")):
                abort(409)
            result={"verified":"Verified","false":"Dismissed","failed":"Reopened"}[action]
            run("UPDATE issues SET status=?,reviewed=? WHERE id=?",result,int(action!="failed"),i)
            # Score each ballot only once, including after repeated review attempts.
            tag=f" · case {i} / round {x['cycle']}"
            if one("SELECT 1 FROM inspections WHERE issue=? AND cycle=?",i,x["cycle"]):
                abort(409)
            run("INSERT INTO inspections VALUES(?,?)",i,x["cycle"])
            if action!="failed":
                for m in members(i):
                    run("INSERT INTO ledger(user,delta,reason) VALUES(?,?,?)",m["user"],-8 if action=="false" else 5,("False claim after review" if action=="false" else "Report corroborated by registrar")+tag)
            for v in rows("SELECT * FROM votes WHERE issue=? AND cycle=?",i,x["cycle"]):
                correct=(v["choice"]=="confirm")==(action=="verified")
                if action!="false":
                    run("INSERT INTO ledger(user,delta,reason) VALUES(?,?,?)",v["user"],3 if correct else -3,("Vote matched inspection" if correct else "Vote contradicted inspection")+tag)
            event(i,"Registrar inspection recorded · "+result+".")
        else:
            abort(404)
        return finish("Issue updated.","detail",i=i)

    return app

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="ReportFix campus desk")
    parser.add_argument("--port",type=int,default=5080)
    parser.add_argument("--approve-registrar",metavar="EMAIL",help="Approve a registered registrar account")
    args=parser.parse_args()
    app=create_app()
    if args.approve_registrar:
        with app.app_context():
            updated=db().execute(
                "UPDATE users SET approved=1,dept='Registrar' WHERE name=? AND role='Admin' AND approved=0",
                (args.approve_registrar.strip().casefold(),),
            )
            if not updated.rowcount:
                parser.error("No pending registrar account with that email.")
            db().commit()
            print("Registrar access approved.")
    else:
        start_delivery(app)
        app.run(host="127.0.0.1",port=args.port,debug=False)
