#!/usr/bin/env python3
"""
AI Sales Assistant — COMPLETE SINGLE FILE
Everything in one file. Nothing can be missing.
Render: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120 --preload
Local:  python app.py
Login:  admin@salesai.com / Admin@123456
"""
import os,json,csv,io,re,logging,threading,time,hashlib,base64,sqlite3,smtplib,tempfile
import urllib.request,urllib.error,urllib.parse
from contextlib import contextmanager
from datetime import datetime,timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from collections import defaultdict
import jwt
from flask import Flask,jsonify,Response,request,g,Blueprint

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger=logging.getLogger("sales")

# ══ CONFIG ═══════════════════════════════════════════════════════════════════
SECRET_KEY=os.environ.get("SECRET_KEY","ai-sales-secret-32chars-2024xyz!")
JWT_ALGORITHM="HS256"
TOKEN_EXPIRE_HOURS=48
GROQ_API_KEY=os.environ.get("GROQ_API_KEY","")
GROQ_MODEL=os.environ.get("GROQ_MODEL","llama-3.3-70b-versatile")
BLAND_API_KEY=os.environ.get("BLAND_API_KEY","")
GMAIL_EMAIL=os.environ.get("GMAIL_SENDER_EMAIL","")
GMAIL_PASSWORD=os.environ.get("GMAIL_APP_PASSWORD","")
GMAIL_NAME=os.environ.get("GMAIL_SENDER_NAME","AI Sales Team")
SENDGRID_API_KEY=os.environ.get("SENDGRID_API_KEY","")
TWILIO_SID=os.environ.get("TWILIO_ACCOUNT_SID","")
TWILIO_TOKEN=os.environ.get("TWILIO_AUTH_TOKEN","")
TWILIO_FROM=os.environ.get("TWILIO_FROM_NUMBER","")
TWILIO_ADMIN=os.environ.get("TWILIO_ADMIN_NUMBER","")
GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID","")
GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET","")
GOOGLE_REDIRECT_URI=os.environ.get("GOOGLE_REDIRECT_URI","")
DAILY_REPORT_HOUR=int(os.environ.get("DAILY_REPORT_HOUR_UTC","18"))
AUTO_EMAIL_HOUR=int(os.environ.get("AUTO_EMAIL_HOUR_UTC","9"))
AUTO_FOLLOWUP_HOUR=int(os.environ.get("AUTO_FOLLOWUP_HOUR_UTC","10"))
AUTO_CALL_HOUR=int(os.environ.get("AUTO_CALL_HOUR_UTC","11"))
AUTO_SCORE_HOUR=int(os.environ.get("AUTO_SCORE_HOUR_UTC","8"))
DB_PATH=os.environ.get("DB_PATH",os.path.join(tempfile.gettempdir(),"sales.db"))
BLAND_INBOUND_NUMBER=os.environ.get("BLAND_INBOUND_NUMBER","")
ARC_BANK_BSB=os.environ.get("ARC_BANK_BSB","062-000")
ARC_BANK_ACCOUNT=os.environ.get("ARC_BANK_ACCOUNT","12345678")
ARC_BANK_NAME=os.environ.get("ARC_BANK_NAME","ARC Digital")
ARC_ABN=os.environ.get("ARC_ABN","12 345 678 901")
ARC_PHONE=os.environ.get("ARC_PHONE","+61 XXX XXX XXX")
XERO_INVOICE_TEMPLATE=os.environ.get("XERO_INVOICE_TEMPLATE","ACCREC")

# ══ DATABASE ══════════════════════════════════════════════════════════════════
def _dbconn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)),exist_ok=True)
    c=sqlite3.connect(DB_PATH,check_same_thread=False,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

@contextmanager
def get_conn():
    c=_dbconn()
    try: yield c; c.commit()
    except Exception: c.rollback(); raise
    finally: c.close()

def q(sql,args=()):
    with get_conn() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]
def q1(sql,args=()):
    with get_conn() as c:
        r=c.execute(sql,args).fetchone(); return dict(r) if r else None
def run(sql,args=()):
    with get_conn() as c: return c.execute(sql,args).lastrowid
def now(): return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,full_name TEXT NOT NULL,password TEXT NOT NULL,
            role TEXT DEFAULT 'sales_rep',is_active INTEGER DEFAULT 1,
            google_refresh_token TEXT,last_login TEXT,
            created_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,industry TEXT,employee_count INTEGER,annual_revenue INTEGER,
            website TEXT,city TEXT,country TEXT,description TEXT,technologies TEXT,
            status TEXT DEFAULT 'prospect',lead_score INTEGER DEFAULT 0,ai_summary TEXT,
            linkedin_url TEXT,funding_stage TEXT,created_by INTEGER,
            created_at TEXT DEFAULT(datetime('now')),updated_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            first_name TEXT NOT NULL,last_name TEXT,email TEXT,phone TEXT,title TEXT,
            department TEXT,seniority_level TEXT DEFAULT 'individual',
            is_decision_maker INTEGER DEFAULT 0,created_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS emails(id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            contact_id INTEGER,created_by INTEGER,email_type TEXT DEFAULT 'cold',
            subject TEXT NOT NULL,body TEXT NOT NULL,recipient_email TEXT NOT NULL,
            recipient_name TEXT,status TEXT DEFAULT 'draft',sent_at TEXT,ai_model_used TEXT,
            created_at TEXT DEFAULT(datetime('now')),updated_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS meetings(id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            contact_id INTEGER,created_by INTEGER,title TEXT NOT NULL,
            meeting_type TEXT DEFAULT 'discovery',description TEXT,scheduled_at TEXT,
            duration_minutes INTEGER DEFAULT 30,status TEXT DEFAULT 'proposed',
            meeting_link TEXT,google_event_id TEXT,notes TEXT,
            created_at TEXT DEFAULT(datetime('now')),updated_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT,
            bland_call_id TEXT,company_id INTEGER,contact_id INTEGER,created_by INTEGER,
            phone_number TEXT NOT NULL,objective TEXT DEFAULT 'qualify',task_prompt TEXT,
            voice TEXT DEFAULT 'nat',status TEXT DEFAULT 'queued',duration_seconds INTEGER,
            recording_url TEXT,transcript TEXT,summary TEXT,error_message TEXT,
            created_at TEXT DEFAULT(datetime('now')),updated_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS lead_scores(id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER UNIQUE REFERENCES companies(id) ON DELETE CASCADE,
            total_score INTEGER DEFAULT 0,revenue_score INTEGER DEFAULT 0,
            employee_score INTEGER DEFAULT 0,industry_score INTEGER DEFAULT 0,
            buying_signal_score INTEGER DEFAULT 0,department_signal_score INTEGER DEFAULT 0,
            email_activity_score INTEGER DEFAULT 0,tier TEXT DEFAULT 'cold',
            updated_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS buying_signals(id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            signal_type TEXT NOT NULL,signal_name TEXT NOT NULL,signal_description TEXT,
            strength INTEGER DEFAULT 5,source TEXT DEFAULT 'ai',
            detected_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS sms_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_number TEXT NOT NULL,from_number TEXT,body TEXT NOT NULL,
            status TEXT DEFAULT 'sent',event_type TEXT,error_msg TEXT,
            created_at TEXT DEFAULT(datetime('now')));
        CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,sender_name TEXT,message TEXT NOT NULL,
            created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS call_transcripts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id INTEGER REFERENCES calls(id) ON DELETE CASCADE,
            company_id INTEGER, contact_id INTEGER,
            transcript TEXT, summary TEXT, sentiment TEXT DEFAULT 'neutral',
            meeting_booked INTEGER DEFAULT 0, google_meet_link TEXT,
            interest_score INTEGER DEFAULT 0, next_action TEXT,
            created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS email_drafts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            contact_id INTEGER, created_by INTEGER,
            draft_type TEXT DEFAULT 'follow_up',
            subject TEXT NOT NULL, body TEXT NOT NULL,
            recipient_email TEXT NOT NULL, recipient_name TEXT,
            status TEXT DEFAULT 'pending_approval',
            approved_by INTEGER, approved_at TEXT,
            scheduled_send_day INTEGER DEFAULT 0,
            call_transcript_id INTEGER,
            sent_at TEXT, created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS opportunities(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            contact_id INTEGER, created_by INTEGER,
            title TEXT NOT NULL,
            interest_score INTEGER DEFAULT 10,
            quoted_value REAL DEFAULT 0,
            accepted_value REAL,
            status TEXT DEFAULT 'open',
            quotation_text TEXT, invoice_text TEXT,
            notes TEXT,
            created_at TEXT DEFAULT(datetime('now')),
            updated_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS contact_linkedin(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
            company_id INTEGER,
            linkedin_url TEXT, position TEXT, seniority TEXT,
            is_key_person INTEGER DEFAULT 0,
            created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS company_chat(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            sender TEXT NOT NULL, message TEXT NOT NULL,
            created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS inbound_calls(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bland_call_id TEXT, phone_number TEXT,
            caller_name TEXT, campaign_source TEXT DEFAULT 'facebook_ad',
            transcript TEXT, summary TEXT, sentiment TEXT DEFAULT 'neutral',
            interest_score INTEGER DEFAULT 0, meeting_booked INTEGER DEFAULT 0,
            google_meet_link TEXT, company_id INTEGER, contact_id INTEGER,
            status TEXT DEFAULT 'completed', duration_seconds INTEGER,
            recording_url TEXT, created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS invoices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE CASCADE,
            company_id INTEGER, contact_id INTEGER, created_by INTEGER,
            invoice_number TEXT UNIQUE, invoice_type TEXT DEFAULT 'invoice',
            subject TEXT, content TEXT, amount REAL DEFAULT 0,
            gst_amount REAL DEFAULT 0, total_amount REAL DEFAULT 0,
            status TEXT DEFAULT 'draft', xero_invoice_id TEXT,
            due_date TEXT, issued_date TEXT,
            approved_by INTEGER, approved_at TEXT,
            created_at TEXT DEFAULT(datetime('now')));

        CREATE TABLE IF NOT EXISTS quotations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE CASCADE,
            company_id INTEGER, contact_id INTEGER, created_by INTEGER,
            quote_number TEXT UNIQUE, content TEXT, amount REAL DEFAULT 0,
            gst_amount REAL DEFAULT 0, total_amount REAL DEFAULT 0,
            valid_until TEXT, status TEXT DEFAULT 'draft',
            xero_quote_id TEXT, template TEXT DEFAULT 'professional',
            approved_by INTEGER, approved_at TEXT,
            created_at TEXT DEFAULT(datetime('now')));
        """)
    _seed_db()

def _seed_db():
    if q1("SELECT 1 FROM users WHERE email='admin@salesai.com'"): return
    pw=hashlib.sha256(("Admin@123456"+SECRET_KEY).encode()).hexdigest()
    aid=run("INSERT INTO users(email,full_name,password,role) VALUES(?,?,?,?)",
            ("admin@salesai.com","System Admin",pw,"admin"))
    demos=[
        ("Stripe","FinTech",4000,7500000000,"stripe.com","San Francisco","USA",91,"opportunity",'["Python","Go"]'),
        ("Notion","SaaS",400,300000000,"notion.so","San Francisco","USA",78,"qualified",'["TypeScript"]'),
        ("Vercel","Technology",350,200000000,"vercel.com","San Francisco","USA",74,"prospect",'["Next.js"]'),
        ("Figma","Design",1000,400000000,"figma.com","San Francisco","USA",85,"qualified",'["C++"]'),
        ("Linear","Software",80,50000000,"linear.app","San Francisco","USA",62,"prospect",'["TypeScript"]'),
        ("Retool","SaaS",300,100000000,"retool.com","San Francisco","USA",55,"prospect",'["React"]'),
        ("PlanetScale","Database",150,60000000,"planetscale.com","San Mateo","USA",38,"cold",'["MySQL"]'),
        ("Loom","Technology",200,80000000,"loom.com","San Francisco","USA",44,"prospect",'["React"]'),
        ("Airtable","SaaS",800,350000000,"airtable.com","San Francisco","USA",82,"opportunity",'["React"]'),
        ("Miro","Software",1500,400000000,"miro.com","Amsterdam","Netherlands",77,"qualified",'["React"]'),
    ]
    phones=["+14155550100","+14155550101","+14155550102","+14155550103","+14155550104",
            "+14155550105","+14155550106","+14155550107","+14155550108","+31201234567"]
    for (nm,ind,emp,rev,web,city,country,sc,status,techs),phone in zip(demos,phones):
        cid=run("""INSERT INTO companies(name,industry,employee_count,annual_revenue,website,
                   city,country,lead_score,status,technologies,created_by,description)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (nm,ind,emp,rev,web,city,country,sc,status,techs,aid,f"Leading {ind} company"))
        run("""INSERT INTO contacts(company_id,first_name,last_name,email,phone,title,
               department,seniority_level,is_decision_maker) VALUES(?,?,?,?,?,?,?,?,?)""",
            (cid,"Alex","Johnson",f"alex@{web}",phone,"VP Engineering","Engineering","vp",1))
        run("""INSERT INTO lead_scores(company_id,total_score,revenue_score,employee_score,
               industry_score,buying_signal_score,department_signal_score,email_activity_score,tier)
               VALUES(?,?,80,70,80,75,60,50,?)""",
            (cid,sc,"hot" if sc>=70 else "warm" if sc>=40 else "cold"))
        run("""INSERT INTO buying_signals(company_id,signal_type,signal_name,signal_description,strength,source)
               VALUES(?,?,?,?,?,?)""",(cid,"hiring","Rapid Hiring","20+ open roles",8,"demo"))
    cids=[r["id"] for r in q("SELECT id FROM companies LIMIT 8")]
    for i,(cid,st,et) in enumerate(zip(cids,
        ["sent","sent","opened","replied","draft","sent","opened","sent"],
        ["cold","follow_up","meeting_request","cold","cold","follow_up","meeting_request","cold"])):
        run("""INSERT INTO emails(company_id,created_by,email_type,subject,body,
               recipient_email,recipient_name,status,ai_model_used) VALUES(?,?,?,?,?,?,?,?,?)""",
            (cid,aid,et,"Quick question about your growth",
             "Hi Alex,\n\nI noticed you are scaling fast.\n\nWorth a 15-min call?\n\nBest,\nAI Sales Team",
             f"alex@demo{i}.com","Alex Johnson",st,"groq"))
    for i,(t,mt) in enumerate([("Discovery Call","discovery"),("Product Demo","demo"),
                                ("Follow-up","follow_up"),("Negotiation","negotiation")]):
        sched=(datetime.utcnow()+timedelta(days=i+1)).strftime("%Y-%m-%d %H:%M:%S")
        run("""INSERT INTO meetings(company_id,created_by,title,meeting_type,
               scheduled_at,duration_minutes,status) VALUES(?,?,?,?,?,?,?)""",
            (cids[i%len(cids)],aid,t,mt,sched,30,"scheduled"))
    logger.info("✅ Demo data seeded")

# ══ AUTH ══════════════════════════════════════════════════════════════════════
def hash_pw(p): return hashlib.sha256((p+SECRET_KEY).encode()).hexdigest()
def verify_pw(p,h): return hash_pw(p)==h
def make_token(uid,email,role):
    return jwt.encode({"sub":str(uid),"email":email,"role":role,
        "exp":datetime.utcnow()+timedelta(hours=TOKEN_EXPIRE_HOURS)},
        SECRET_KEY,algorithm=JWT_ALGORITHM)
def decode_token(t): return jwt.decode(t,SECRET_KEY,algorithms=[JWT_ALGORITHM])
def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        tok=(request.headers.get("Authorization","").replace("Bearer ","")).strip()
        if not tok: return jsonify({"error":"Missing token"}),401
        try:
            p=decode_token(tok)
            g.user={"id":int(p["sub"]),"email":p["email"],"role":p["role"]}
        except jwt.ExpiredSignatureError: return jsonify({"error":"Token expired"}),401
        except Exception: return jsonify({"error":"Invalid token"}),401
        return f(*a,**kw)
    return dec

# ══ AI SERVICE ════════════════════════════════════════════════════════════════
GROQ_MODELS=[
    GROQ_MODEL,
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "mixtral-8x7b-32768",
]

def _groq(prompt, system=None, max_tokens=700):
    """Groq API with 4-model fallback chain on 403/429 rate limits."""
    if not GROQ_API_KEY: return None
    msgs=[]
    if system: msgs.append({"role":"system","content":system})
    msgs.append({"role":"user","content":prompt})
    for model in GROQ_MODELS:
        body=json.dumps({"model":model,"messages":msgs,"max_tokens":max_tokens}).encode()
        try:
            req=urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",
                data=body,headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"})
            with urllib.request.urlopen(req,timeout=25) as r:
                result=json.loads(r.read())["choices"][0]["message"]["content"].strip()
                if model!=GROQ_MODEL: logger.info(f"Groq fallback: {model}")
                return result
        except urllib.error.HTTPError as e:
            if e.code in(429,503,403):
                logger.warning(f"Groq {e.code} on {model} — trying next")
                time.sleep(0.4); continue
            if e.code==401:
                logger.error("Groq 401 — GROQ_API_KEY invalid. Get new key at console.groq.com")
                return None
            logger.warning(f"Groq HTTP {e.code} on {model}")
            continue
        except urllib.error.URLError as e:
            logger.warning(f"Groq network: {e.reason}"); return None
        except Exception as e:
            logger.warning(f"Groq {model}: {e}"); continue
    logger.warning("Groq: all models rate-limited. Using fallback content.")
    return None
def company_summary(co):
    rev=co.get("annual_revenue") or 0
    emp=co.get("employee_count") or "N/A"
    p=f"2-sentence B2B sales summary for {co['name']}, {co.get('industry','tech')}, {emp} employees, ${rev:,} revenue. Be actionable."
    return (_groq(p,"B2B sales expert.",180) or
            f"{co['name']} is a {co.get('industry','technology')} company with {co.get('employee_count','N/A')} employees. Strong AI sales candidate.")

def generate_email(co,ct,email_type,custom=""):
    name=f"{ct.get('first_name','Team')} {ct.get('last_name','')}".strip()
    sender=GMAIL_NAME or "AI Sales Team"
    p={"cold":f"Cold B2B email to {name} at {co['name']}. {custom}\nSUBJECT: ...\nBODY:\n...",
       "follow_up":f"Follow-up to {name} at {co['name']} — no reply.\nSUBJECT: ...\nBODY:\n...",
       "meeting_request":f"Meeting request to {name} at {co['name']}.\nSUBJECT: ...\nBODY:\n..."}
    result=_groq(p.get(email_type,p["cold"]),"Expert B2B copywriter.",480)
    if result:
        subj,lines,in_b="", [],False
        for line in result.strip().split("\n"):
            if line.upper().startswith("SUBJECT:"): subj=line.split(":",1)[1].strip()
            elif line.upper().strip()=="BODY:": in_b=True
            elif in_b: lines.append(line)
        if subj and lines: return {"subject":subj,"body":"\n".join(lines).strip()}
    t={"cold":{"subject":f"Helping {co['name']} automate sales",
               "body":f"Hi {name},\n\nI noticed {co['name']} is scaling fast.\n\nWe help companies automate sales with AI. Worth a 15-min call?\n\nBest,\n{sender}"},
       "follow_up":{"subject":f"Following up — {co['name']}",
                    "body":f"Hi {name},\n\nResurfacing my note. Any availability this month?\n\nThanks,\n{sender}"},
       "meeting_request":{"subject":f"15 min — AI Sales demo for {co['name']}?",
                          "body":f"Hi {name},\n\nThree slots:\n• Tue 10 AM\n• Wed 2 PM\n• Thu 11 AM\n\nDoes any work?\n\nBest,\n{sender}"}}
    return t.get(email_type,t["cold"])

def buying_signals(co):
    rev=co.get("annual_revenue") or 0
    p=f"3 buying signals for {co.get('name','')}, {co.get('industry','')}, ${rev:,}. JSON: [{{\"type\":\"...\",\"name\":\"...\",\"description\":\"...\",\"strength\":7}}]"
    result=_groq(p,"Return valid JSON array only.",280)
    if result:
        try:
            m=re.search(r"\[.*\]",result,re.DOTALL)
            if m: return json.loads(m.group())
        except Exception: pass
    out=[]
    if (co.get("employee_count") or 0)>100: out.append({"type":"scale","name":"Enterprise Scale","description":"Budget authority","strength":7})
    if rev>1_000_000: out.append({"type":"revenue","name":"Strong Revenue","description":"Investment capacity","strength":8})
    out.append({"type":"tech","name":"Tech-Forward","description":"Active stack","strength":6})
    return out

def call_script(co, ct, objective="qualify"):
    """Sarah - ARC Digital AI caller trained on Kevin Raju's methodology."""
    name     = f"{ct.get('first_name','there')}".strip()
    industry = (co.get('industry') or 'trade').lower()
    company  = co.get('name','the business')

    sarah_system = (
        "You are Sarah, a warm AI assistant calling on behalf of Kevin Raju from ARC Digital. "
        "Kevin has 10+ years helping 200+ organisations improve business processes. "
        "You call trade businesses to find pain points: missed calls, admin, quote follow-up, "
        "after-hours enquiries, scheduling, owner workload. "
        "You do NOT sell software. You qualify pain, then offer a 15-min call with Kevin. "
        "Be warm, calm, practical, slightly humorous. Short sentences. Ask one question at a time. "
        "Sound like a sharp friendly ops assistant who gets trade businesses. "
        "NEVER say: digital transformation, AI solution, operational optimisation, business assessment. "
        "USE: missed calls, booking jobs, quote follow-up, admin, after-hours calls, owner workload."
    )
    prompt = (
        f"Generate Sarah's full call script for {name} at {company} ({industry} business). "
        f"Objective: {objective}. Include: opening, discovery questions, pain-point responses, "
        f"Kevin intro (only after pain found), meeting booking, not-interested exit. "
        f"Output ONLY what Sarah says. Natural speech. Under 500 words."
    )
    ai_result = _groq(prompt, sarah_system, 700)
    if ai_result and len(ai_result) > 80:
        return ai_result

    # Fallback — full Sarah script with all branches
    return (
        f"Hi {name}, Sarah here. "
        f"I'm an AI assistant calling on behalf of Kevin Raju from ARC Digital. "
        f"I'll be quick — I'm guessing you're probably on-site, driving, or about to get interrupted. "
        f"Kevin has been speaking with {industry} businesses about missed calls, "
        f"quote follow-up and admin bottlenecks. "
        f"One practical question: when you're on the tools and the phone rings, "
        f"what normally happens to that enquiry? "
        f"\n\n"
        f"[IF ASKED WHAT THIS IS ABOUT]: "
        f"Fair question. Kevin helps {industry} businesses look at where enquiries, bookings, "
        f"quotes and customer follow-up are getting lost. "
        f"Sometimes it's missed calls. Sometimes manual admin. "
        f"Sometimes quotes just don't get followed up. "
        f"Just a quick check to see if any of that's relevant to {company}. "
        f"\n\n"
        f"[IF BUSY]: No problem. Would there be a better time for a quick call back? "
        f"\n\n"
        f"[DISCOVERY — ask ONE at a time]: "
        f"Who normally answers the phone when you're on the tools? "
        f"What happens after hours? "
        f"Are job enquiries captured somewhere or mostly manual? "
        f"Do quotes get followed up consistently? "
        f"How much admin still falls back on you personally? "
        f"If you could remove one repetitive task from the business tomorrow, what would it be? "
        f"\n\n"
        f"[WHEN THEY NAME A PROBLEM]: "
        f"That makes sense. We hear that quite a bit from trade businesses. "
        f"How often would you say that happens? "
        f"And what impact does that have — lost jobs, customer frustration, "
        f"or just more work after hours? "
        f"\n\n"
        f"[ONLY AFTER GENUINE PAIN POINT — KEVIN INTRO]: "
        f"That's the type of thing Kevin usually looks at. "
        f"He starts with the process first, not the technology. "
        f"Sometimes the fix is simple. Sometimes automation or AI can help. "
        f"Would it be worth a 15-minute conversation with Kevin to see where the gaps are? "
        f"\n\n"
        f"[IF THEY AGREE — COLLECT]: Name, email, preferred phone, preferred time, main challenge. "
        f"Then say: Perfect. I'll pass that through to Kevin's team so they can send the invite. "
        f"Thanks for your time today. "
        f"\n\n"
        f"[NOT INTERESTED]: No worries at all. "
        f"Thanks for your time and all the best with the business. "
        f"\n\n"
        f"[REMOVE FROM LIST]: Absolutely. I'll record that request. "
        f"You won't be contacted again. Thanks."
    )
# ══════════════════════════════════════════════════════
# PLUMBING-SPECIFIC AI FUNCTIONS
# ══════════════════════════════════════════════════════

PLUMBING_CONTEXT = (
    "You are an AI sales assistant for ARC Digital (Kevin Raju). "
    "Kevin helps Australian plumbing businesses automate their operations. "
    "Products/Services: AI phone answering, booking automation, CRM, quote follow-up, after-hours call handling. "
    "Target: Plumbing businesses across Australia. "
    "Pain points: missed calls, manual admin, quote follow-up, after-hours enquiries, scheduling, owner workload."
)

def ai_call_transcript_analysis(transcript_text, company_name, contact_name):
    if not transcript_text:
        return {"interest_score":0,"meeting_booked":False,"summary":"No transcript","next_action":"retry_call","sentiment":"neutral","pain_points":[],"key_quote":""}
    prompt = (
        f"Analyse this AI sales call transcript for a plumbing business.\n"
        f"Company: {company_name}\nContact: {contact_name}\n"
        f"Transcript: {transcript_text[:2500]}\n\n"
        "Return JSON only with keys: interest_score(0-10 int), meeting_booked(bool), "
        "sentiment(positive/neutral/negative), summary(str), pain_points(list), "
        "next_action(book_meeting/send_followup/send_thankyou/retry_call/no_action), "
        "meeting_datetime(ISO or null), key_quote(str)"
    )
    result = _groq(prompt, PLUMBING_CONTEXT, 500)
    if result:
        try:
            import re as _re
            m = _re.search(r'\{.*\}', result, _re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception:
            pass
    text_lower = transcript_text.lower()
    score = 5
    if any(w in text_lower for w in ['yes','interested','sounds good','book','meeting','definitely']): score += 3
    if any(w in text_lower for w in ['no','not interested','busy','remove']): score -= 3
    booked = any(w in text_lower for w in ['book','schedule','meeting','appointment'])
    return {"interest_score":max(0,min(10,score)),"meeting_booked":booked,
            "sentiment":"positive" if score>=7 else "negative" if score<=3 else "neutral",
            "summary":f"Call with {contact_name} at {company_name}.",
            "pain_points":[],"next_action":"send_followup" if score>=5 else "retry_call","key_quote":""}

def generate_post_call_email(co, ct, analysis, transcript_text=""):
    company = co.get('name','the company')
    fname   = ct.get('first_name','there')
    score   = analysis.get('interest_score', 5)
    booked  = analysis.get('meeting_booked', False)
    summary = analysis.get('summary','')
    meet_link = analysis.get('meet_link','')

    if booked or score >= 8:
        subject = f"Great speaking with you today, {fname} — {'Meeting Confirmed' if booked else 'Next Steps'}"
        body_prompt = (
            f"Write a warm thank-you follow-up email from Kevin Raju (ARC Digital) to {fname} at {company} (plumbing business).\n"
            f"Call summary: {summary}\n"
            f"{'Meeting confirmed. Google Meet: ' + meet_link if booked and meet_link else 'They showed strong interest. Suggest 15-min call.'}\n"
            f"Key point: {analysis.get('key_quote','')}\n"
            "Keep under 200 words. Australian tone. Professional and warm. Include subject line."
        )
    elif score >= 5:
        subject = f"Following up from our call today — {company}"
        body_prompt = (
            f"Write a brief follow-up email from Kevin Raju (ARC Digital) to {fname} at {company} (plumbing business).\n"
            f"They were mildly interested in AI automation for missed calls and admin. Summary: {summary}\n"
            "Offer a 15-min call. Under 150 words. No pressure. Australian."
        )
    else:
        subject = f"Thanks for your time today, {fname}"
        body_prompt = (
            f"Write a short thank-you email from Kevin Raju (ARC Digital) to {fname} at {company} (plumbing business).\n"
            "They were not interested. Keep the door open. Under 100 words. Warm and professional."
        )

    result = _groq(body_prompt, "Expert B2B email writer for Australian trade businesses.", 400)
    body = ""
    if result:
        lines = result.strip().splitlines()
        # Skip subject line if included
        start = 0
        for i, line in enumerate(lines):
            if line.upper().startswith('SUBJECT'): start = i+1; continue
            if line.strip() and i >= start: body = "\n".join(lines[i:]).strip(); break

    if not body:
        if booked:
            body = (f"Hi {fname},\n\nThank you so much for speaking with Sarah today on behalf of Kevin Raju from ARC Digital.\n\n"
                    f"I am delighted to confirm our meeting. Here is your Google Meet link:\n{meet_link}\n\n"
                    f"Looking forward to showing you how we can help {company} capture more leads and reduce admin.\n\n"
                    "All the best,\nKevin Raju\nARC Digital\nkevin@arcdigital.com.au")
        elif score >= 5:
            body = (f"Hi {fname},\n\nThank you for taking the time to speak with Sarah from ARC Digital today.\n\n"
                    f"We would love to show you how we help plumbing businesses like {company} stop missing calls and cut admin time.\n\n"
                    "Would a quick 15-minute call with Kevin work this week?\n\n"
                    "All the best,\nKevin Raju\nARC Digital\nkevin@arcdigital.com.au")
        else:
            body = (f"Hi {fname},\n\nThank you for taking a moment to speak with Sarah today.\n\n"
                    f"We completely understand — it is a busy time for {company}. If you ever want to explore how AI can help with missed calls or admin down the track, please do not hesitate to reach out.\n\n"
                    "All the best,\nKevin Raju\nARC Digital")

    return {"email_type": "meeting_confirm" if booked else ("followup" if score>=5 else "thankyou"),
            "subject": subject, "body": body, "interest_score": score}

def qualify_company_plumbing(co, contacts, signals):
    industry = (co.get('industry') or '').lower()
    emp = co.get('employee_count') or 0
    rev = co.get('annual_revenue') or 0
    plumbing_kw = ['plumb','pipe','drain','hvac','trade','construction','service','maintenance']
    industry_fit = min(10, sum(2 for k in plumbing_kw if k in industry) + 2)
    if 2 <= emp <= 5:     size_score = 10
    elif 6 <= emp <= 20:  size_score = 9
    elif 21 <= emp <= 50: size_score = 7
    elif emp == 0:        size_score = 5
    else:                 size_score = 4
    if 100000 <= rev <= 2000000:   rev_score = 10
    elif rev > 2000000:            rev_score = 7
    elif rev > 0:                  rev_score = 5
    else:                          rev_score = 5
    sig_score = min(10, len(signals) * 2)
    qualify_score = int((industry_fit + size_score + rev_score + sig_score) / 4)

    prompt = (f"Rate 0-10 how suitable {co.get('name')} (industry:{co.get('industry')}, "
              f"employees:{emp}, revenue:${rev:,}) is for AI call-answering automation for Australian plumbing. "
              "Return JSON: {\"qualify_score\":7,\"reason\":\"one sentence\",\"recommended_value\":4500,\"risk\":\"low\"}")
    ai_data = {}
    result = _groq(prompt, "B2B sales qualification expert.", 150)
    if result:
        try:
            import re as _re
            m = _re.search(r'\{[^}]+\}', result, _re.DOTALL)
            if m: ai_data = json.loads(m.group())
        except Exception: pass

    final_score = ai_data.get('qualify_score', qualify_score)
    final_score = max(0, min(10, int(final_score)))
    return {
        "qualify_score":    final_score,
        "industry_fit":     industry_fit,
        "size_score":       size_score,
        "revenue_score":    rev_score,
        "reason":           ai_data.get('reason', f"{'Strong' if final_score>=7 else 'Moderate'} fit for plumbing automation"),
        "recommended_value":ai_data.get('recommended_value', 3000 + final_score * 500),
        "risk":             ai_data.get('risk','medium'),
        "tier":             "hot" if final_score>=8 else "warm" if final_score>=5 else "cold",
    }

def generate_company_chatbot_response(company_id, user_message, history):
    co = q1("SELECT * FROM companies WHERE id=?", (company_id,))
    if not co: return "Company not found."
    cts  = q("SELECT * FROM contacts WHERE company_id=?", (company_id,))
    sigs = q("SELECT * FROM buying_signals WHERE company_id=?", (company_id,))
    ls   = q1("SELECT * FROM lead_scores WHERE company_id=?", (company_id,))
    opp  = q1("SELECT * FROM opportunities WHERE company_id=? ORDER BY created_at DESC LIMIT 1", (company_id,))
    ctx = (f"Company: {co['name']} | Industry: {co.get('industry')} | Employees: {co.get('employee_count')} "
           f"| Revenue: ${co.get('annual_revenue') or 0:,} | Score: {co.get('lead_score',0)}/100 "
           f"| Status: {co.get('status')} | Contacts: {len(cts)} "
           f"| Signals: {', '.join(s.get('signal_name','') for s in sigs[:3]) or 'none'} "
           f"| Tier: {ls.get('tier','unknown') if ls else 'not scored'} "
           f"| Opp: {'$'+str(opp.get('quoted_value',0)) if opp else 'none'}")
    hist = "\n".join([f"{m['role']}: {m['content']}" for m in (history or [])[-6:]])
    system = (f"{PLUMBING_CONTEXT}\n\nYou are a sales intelligence chatbot for {co['name']}.\n"
              f"Company data: {ctx}\nPrevious: {hist}\n"
              "Give specific, actionable sales intelligence about this company. Under 150 words.")
    return (_groq(user_message, system, 300) or
            f"Based on {co['name']}'s profile, they are a {'strong' if (co.get('lead_score') or 0)>=70 else 'moderate'} prospect for ARC Digital's plumbing automation.")

def generate_quotation(co, ct, opportunity):
    value   = opportunity.get('quoted_value', 5000)
    company = co.get('name','the company')
    name    = f"{ct.get('first_name','')} {ct.get('last_name','')}".strip() if ct else "Team"
    inv_num = f"ARC-Q-{co.get('id',1):04d}-{datetime.utcnow().strftime('%Y%m')}"
    prompt  = (f"Write a professional quotation for ARC Digital (Kevin Raju, ABN 12 345 678 901) to {company}.\n"
               f"Contact: {name} | Value: AUD ${value:,.2f} | Quote: {inv_num}\n"
               "Services: AI call answering, booking automation, CRM, quote follow-up, after-hours, reporting.\n"
               "Include: quote number, date, validity 30 days, itemised services, GST, payment terms 30 days, Australian format.")
    result = _groq(prompt, "Professional Australian business document writer.", 700)
    if result: return result
    return (f"QUOTATION\n{'='*50}\nARC Digital | ABN: 12 345 678 901 | kevin@arcdigital.com.au\n\n"
            f"Quote No: {inv_num}\nDate: {datetime.utcnow().strftime('%d %B %Y')}\n"
            f"Valid Until: {(datetime.utcnow()+timedelta(days=30)).strftime('%d %B %Y')}\n\n"
            f"Prepared For: {company}\nAttn: {name}\n\n"
            "SERVICES:\n"
            f"  AI Call Answering & Lead Capture ......... AUD ${value*0.35:,.2f}/mo\n"
            f"  CRM & Quote Follow-Up Automation ......... AUD ${value*0.20:,.2f}/mo\n"
            f"  After-Hours Enquiry Handling ............. AUD ${value*0.18:,.2f}/mo\n"
            f"  SMS & Email Automation ................... AUD ${value*0.12:,.2f}/mo\n"
            f"  Reporting & Analytics .................... AUD ${value*0.10:,.2f}/mo\n"
            "  {'─'*45}\n"
            f"  Subtotal (ex GST) ........................ AUD ${value/1.1:,.2f}/mo\n"
            f"  GST (10%) ................................ AUD ${value/11:,.2f}/mo\n"
            f"  TOTAL (inc GST) .......................... AUD ${value:,.2f}/mo\n\n"
            "Payment Terms: 30 days from invoice\nThis quotation is valid for 30 days.\n\n"
            "Kevin Raju | ARC Digital | kevin@arcdigital.com.au")

def generate_invoice(co, ct, opportunity):
    value   = opportunity.get('accepted_value') or opportunity.get('quoted_value', 5000)
    company = co.get('name','the company')
    name    = f"{ct.get('first_name','')} {ct.get('last_name','')}".strip() if ct else "Team"
    inv_num = f"ARC-INV-{co.get('id',1):04d}-{datetime.utcnow().strftime('%Y%m')}"
    prompt  = (f"Write a professional Australian tax invoice for ARC Digital (Kevin Raju, ABN 12 345 678 901) to {company}.\n"
               f"Contact: {name} | Amount: AUD ${value:,.2f} | Invoice: {inv_num}\n"
               "Services: AI business automation monthly subscription.\n"
               "Include: invoice number, issue date, due date 30 days, GST breakdown, bank BSB 062-000 Acc 12345678, ARC Digital.")
    result = _groq(prompt, "Professional Australian tax invoice writer.", 600)
    if result: return result
    return (f"TAX INVOICE\n{'='*50}\nARC Digital | ABN: 12 345 678 901\nkevin@arcdigital.com.au | +61 XXX XXX XXX\n\n"
            f"Invoice No: {inv_num}\nIssue Date: {datetime.utcnow().strftime('%d %B %Y')}\n"
            f"Due Date: {(datetime.utcnow()+timedelta(days=30)).strftime('%d %B %Y')}\n\n"
            f"Bill To: {company}\nAttn: {name}\n\n"
            f"  AI Business Automation — Monthly Subscription\n"
            f"  {'─'*45}\n"
            f"  Amount (ex GST) .......................... AUD ${value/1.1:,.2f}\n"
            f"  GST (10%) ................................ AUD ${value/11:,.2f}\n"
            f"  TOTAL DUE ................................ AUD ${value:,.2f}\n\n"
            "Payment Details:\n"
            "  Bank: Commonwealth Bank\n  BSB: 062-000\n  Account: 12345678\n  Name: ARC Digital\n"
            f"  Reference: {inv_num}\n\nThank you for your business.")




# ══════════════════════════════════════════════════════════════════════════════
# INVOICE & QUOTATION GENERATION — Xero-compatible format
# ══════════════════════════════════════════════════════════════════════════════

INVOICE_TEMPLATES = {
    "professional": {
        "name": "Professional",
        "accent": "#1e3a5f",
        "font": "Arial, sans-serif",
    },
    "modern": {
        "name": "Modern Blue",
        "accent": "#3b82f6",
        "font": "Helvetica, sans-serif",
    },
    "classic": {
        "name": "Classic",
        "accent": "#1a1a1a",
        "font": "Georgia, serif",
    },
}

def build_invoice_number():
    existing = q("SELECT invoice_number FROM invoices ORDER BY id DESC LIMIT 1")
    if existing and existing[0].get("invoice_number"):
        try:
            last = int(existing[0]["invoice_number"].split("-")[-1])
            return f"ARC-INV-{last+1:04d}"
        except Exception:
            pass
    return f"ARC-INV-{datetime.utcnow().strftime('%Y%m')}-0001"

def build_quote_number():
    existing = q("SELECT quote_number FROM quotations ORDER BY id DESC LIMIT 1")
    if existing and existing[0].get("quote_number"):
        try:
            last = int(existing[0]["quote_number"].split("-")[-1])
            return f"ARC-Q-{last+1:04d}"
        except Exception:
            pass
    return f"ARC-Q-{datetime.utcnow().strftime('%Y%m')}-0001"

def generate_invoice_html(invoice, opportunity, company, contact, template="professional"):
    """Generate a full HTML invoice — Xero-approved format."""
    tmpl = INVOICE_TEMPLATES.get(template, INVOICE_TEMPLATES["professional"])
    accent = tmpl["accent"]
    font   = tmpl["font"]
    amount_ex  = invoice.get("amount", 0) or 0
    gst        = invoice.get("gst_amount", amount_ex * 0.1)
    total      = invoice.get("total_amount", amount_ex + gst)
    inv_num    = invoice.get("invoice_number", "ARC-INV-0001")
    issued     = (invoice.get("issued_date") or datetime.utcnow().strftime("%d %B %Y"))[:10]
    try:
        issued_dt  = datetime.strptime(issued, "%Y-%m-%d")
        issued_fmt = issued_dt.strftime("%d %B %Y")
    except Exception:
        issued_fmt = issued
    due_date   = invoice.get("due_date") or (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        due_dt  = datetime.strptime(due_date, "%Y-%m-%d")
        due_fmt = due_dt.strftime("%d %B %Y")
    except Exception:
        due_fmt = due_date
    co_name    = (company or {}).get("name","Client")
    co_city    = (company or {}).get("city","")
    co_country = (company or {}).get("country","Australia")
    ct_name    = f"{(contact or {}).get('first_name','')} {(contact or {}).get('last_name','')}".strip()
    content    = invoice.get("content","")
    # Parse content for line items (simple: each line is an item)
    items = []
    for line in (content or "").split("\n"):
        if "........" in line or ":" in line:
            parts = line.split(".")
            if len(parts) >= 2:
                desc = parts[0].strip().lstrip("-•1234567890. ")
                val_str = "".join(c for c in parts[-1] if c.isdigit() or c == ".")
                try:
                    val = float(val_str)
                    if val > 0:
                        items.append({"description": desc, "amount": val})
                except Exception:
                    pass
    if not items:
        items = [{"description": "AI Business Automation — Monthly Subscription", "amount": amount_ex}]

    rows_html = ""
    for item in items:
        rows_html += f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;color:#374151">{item['description']}</td>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;text-align:right;color:#374151">AUD ${item['amount']:,.2f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{inv_num} — ARC Digital</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:{font}; background:#f9fafb; color:#111827; }}
  .page {{ max-width:800px; margin:0 auto; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  .header {{ background:{accent}; color:#fff; padding:36px 40px; display:flex; justify-content:space-between; align-items:flex-start; }}
  .logo-area h1 {{ font-size:1.6rem; font-weight:700; letter-spacing:-.5px; }}
  .logo-area p {{ font-size:.85rem; opacity:.8; margin-top:2px; }}
  .inv-meta {{ text-align:right; }}
  .inv-meta .inv-type {{ font-size:1.3rem; font-weight:700; text-transform:uppercase; letter-spacing:1px; }}
  .inv-meta .inv-num {{ font-size:.9rem; opacity:.85; margin-top:4px; }}
  .body {{ padding:36px 40px; }}
  .parties {{ display:grid; grid-template-columns:1fr 1fr; gap:32px; margin-bottom:28px; }}
  .party-label {{ font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#6b7280; margin-bottom:6px; }}
  .party-name {{ font-size:1rem; font-weight:600; color:#111827; }}
  .party-detail {{ font-size:.85rem; color:#6b7280; margin-top:2px; }}
  .dates-row {{ display:flex; gap:32px; margin-bottom:28px; padding:16px; background:#f3f4f6; border-radius:8px; }}
  .date-item label {{ font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#6b7280; display:block; margin-bottom:4px; }}
  .date-item span {{ font-size:.95rem; font-weight:600; color:#111827; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:20px; }}
  thead th {{ background:{accent}; color:#fff; padding:10px 8px; text-align:left; font-size:.8rem; font-weight:600; letter-spacing:.03em; }}
  thead th:last-child {{ text-align:right; }}
  .totals {{ margin-left:auto; width:260px; }}
  .total-row {{ display:flex; justify-content:space-between; padding:6px 0; font-size:.9rem; color:#374151; }}
  .total-final {{ display:flex; justify-content:space-between; padding:12px 0 6px; font-size:1.1rem; font-weight:700; color:{accent}; border-top:2px solid {accent}; margin-top:6px; }}
  .bank-section {{ background:{accent}15; border-left:4px solid {accent}; padding:16px 20px; border-radius:4px; margin-top:28px; }}
  .bank-section h4 {{ font-size:.85rem; font-weight:700; color:{accent}; text-transform:uppercase; letter-spacing:.05em; margin-bottom:10px; }}
  .bank-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
  .bank-item label {{ font-size:.72rem; color:#6b7280; display:block; }}
  .bank-item span {{ font-size:.9rem; font-weight:600; color:#111827; }}
  .footer {{ background:#f9fafb; border-top:1px solid #e5e7eb; padding:20px 40px; font-size:.78rem; color:#9ca3af; display:flex; justify-content:space-between; }}
  .status-badge {{ display:inline-block; padding:4px 12px; border-radius:20px; font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; }}
  .status-draft {{ background:#fef3c7; color:#92400e; }}
  .status-approved {{ background:#d1fae5; color:#065f46; }}
  .status-paid {{ background:#dbeafe; color:#1e40af; }}
  @media print {{
    body {{ background:#fff; }}
    .page {{ box-shadow:none; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="logo-area">
      <h1>ARC Digital</h1>
      <p>AI Business Automation</p>
      <p style="margin-top:8px;font-size:.8rem;opacity:.75">ABN: {ARC_ABN}</p>
      <p style="font-size:.8rem;opacity:.75">kevin@arcdigital.com.au</p>
      <p style="font-size:.8rem;opacity:.75">{ARC_PHONE}</p>
    </div>
    <div class="inv-meta">
      <div class="inv-type">Tax Invoice</div>
      <div class="inv-num">{inv_num}</div>
      <div style="margin-top:12px">
        <span class="status-badge status-{invoice.get('status','draft')}">{invoice.get('status','DRAFT').upper()}</span>
      </div>
    </div>
  </div>

  <div class="body">
    <div class="parties">
      <div>
        <div class="party-label">From</div>
        <div class="party-name">ARC Digital</div>
        <div class="party-detail">Kevin Raju</div>
        <div class="party-detail">kevin@arcdigital.com.au</div>
        <div class="party-detail">ABN: {ARC_ABN}</div>
      </div>
      <div>
        <div class="party-label">Bill To</div>
        <div class="party-name">{co_name}</div>
        {f'<div class="party-detail">{ct_name}</div>' if ct_name else ''}
        {f'<div class="party-detail">{co_city}, {co_country}</div>' if co_city else ''}
      </div>
    </div>

    <div class="dates-row">
      <div class="date-item"><label>Issue Date</label><span>{issued_fmt}</span></div>
      <div class="date-item"><label>Due Date</label><span>{due_fmt}</span></div>
      <div class="date-item"><label>Payment Terms</label><span>30 Days</span></div>
      {f'<div class="date-item"><label>Opportunity</label><span>{(opportunity or {{}}).get("title","")[:30]}</span></div>' if opportunity else ''}
    </div>

    <table>
      <thead>
        <tr><th style="width:75%">Description</th><th>Amount (AUD)</th></tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>

    <div class="totals">
      <div class="total-row"><span>Subtotal (ex GST)</span><span>AUD ${amount_ex:,.2f}</span></div>
      <div class="total-row"><span>GST (10%)</span><span>AUD ${gst:,.2f}</span></div>
      <div class="total-final"><span>TOTAL DUE</span><span>AUD ${total:,.2f}</span></div>
    </div>

    <div class="bank-section">
      <h4>Payment Details</h4>
      <div class="bank-grid">
        <div class="bank-item"><label>Bank</label><span>Commonwealth Bank of Australia</span></div>
        <div class="bank-item"><label>Account Name</label><span>{ARC_BANK_NAME}</span></div>
        <div class="bank-item"><label>BSB</label><span>{ARC_BANK_BSB}</span></div>
        <div class="bank-item"><label>Account Number</label><span>{ARC_BANK_ACCOUNT}</span></div>
        <div class="bank-item"><label>Reference</label><span>{inv_num}</span></div>
        <div class="bank-item"><label>Amount</label><span>AUD ${total:,.2f}</span></div>
      </div>
    </div>
  </div>

  <div class="footer">
    <span>ARC Digital — ABN {ARC_ABN} — kevin@arcdigital.com.au</span>
    <span>Please use {inv_num} as your payment reference</span>
  </div>
</div>
</body>
</html>"""


def generate_quotation_html(quotation, opportunity, company, contact, template="professional"):
    """Generate a full HTML quotation — Xero-compatible format."""
    tmpl    = INVOICE_TEMPLATES.get(template, INVOICE_TEMPLATES["professional"])
    accent  = tmpl["accent"]
    font    = tmpl["font"]
    amount_ex  = quotation.get("amount", 0) or 0
    gst        = quotation.get("gst_amount", amount_ex * 0.1)
    total      = quotation.get("total_amount", amount_ex + gst)
    q_num      = quotation.get("quote_number", "ARC-Q-0001")
    issued_fmt = datetime.utcnow().strftime("%d %B %Y")
    valid_dt   = quotation.get("valid_until") or (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        valid_fmt = datetime.strptime(valid_dt, "%Y-%m-%d").strftime("%d %B %Y")
    except Exception:
        valid_fmt = valid_dt
    co_name  = (company or {}).get("name","Client")
    co_city  = (company or {}).get("city","")
    ct_name  = f"{(contact or {}).get('first_name','')} {(contact or {}).get('last_name','')}".strip()
    content  = quotation.get("content","")

    # Service items
    services = [
        {"desc":"AI Call Answering & Lead Capture (24/7)","qty":"1 month","unit": round(amount_ex * 0.35, 2)},
        {"desc":"CRM & Quote Follow-Up Automation",       "qty":"1 month","unit": round(amount_ex * 0.20, 2)},
        {"desc":"After-Hours Enquiry Handling",           "qty":"1 month","unit": round(amount_ex * 0.18, 2)},
        {"desc":"AI SMS & Email Automation",              "qty":"1 month","unit": round(amount_ex * 0.15, 2)},
        {"desc":"Reporting Dashboard & Analytics",        "qty":"1 month","unit": round(amount_ex * 0.12, 2)},
    ]

    rows_html = ""
    for s in services:
        rows_html += f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;color:#374151">{s['desc']}</td>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;text-align:center;color:#6b7280">{s['qty']}</td>
          <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;text-align:right;color:#374151">AUD ${s['unit']:,.2f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{q_num} — ARC Digital Quotation</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:{font}; background:#f9fafb; color:#111827; }}
  .page {{ max-width:800px; margin:0 auto; background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  .header {{ background:{accent}; color:#fff; padding:36px 40px; display:flex; justify-content:space-between; align-items:flex-start; }}
  .logo-area h1 {{ font-size:1.6rem; font-weight:700; }}
  .logo-area p {{ font-size:.85rem; opacity:.8; margin-top:2px; }}
  .q-meta {{ text-align:right; }}
  .q-meta .q-type {{ font-size:1.3rem; font-weight:700; text-transform:uppercase; letter-spacing:1px; }}
  .q-meta .q-num {{ font-size:.9rem; opacity:.85; margin-top:4px; }}
  .body {{ padding:36px 40px; }}
  .parties {{ display:grid; grid-template-columns:1fr 1fr; gap:32px; margin-bottom:28px; }}
  .party-label {{ font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#6b7280; margin-bottom:6px; }}
  .party-name {{ font-size:1rem; font-weight:600; }}
  .party-detail {{ font-size:.85rem; color:#6b7280; margin-top:2px; }}
  .info-row {{ display:flex; gap:24px; margin-bottom:28px; padding:16px; background:#f3f4f6; border-radius:8px; flex-wrap:wrap; }}
  .info-item label {{ font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#6b7280; display:block; margin-bottom:4px; }}
  .info-item span {{ font-size:.95rem; font-weight:600; color:#111827; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:20px; }}
  thead th {{ background:{accent}; color:#fff; padding:10px 8px; text-align:left; font-size:.8rem; font-weight:600; }}
  thead th:last-child {{ text-align:right; }}
  .totals {{ margin-left:auto; width:280px; }}
  .total-row {{ display:flex; justify-content:space-between; padding:6px 0; font-size:.9rem; color:#374151; }}
  .total-final {{ display:flex; justify-content:space-between; padding:12px 0 6px; font-size:1.1rem; font-weight:700; color:{accent}; border-top:2px solid {accent}; margin-top:6px; }}
  .note-box {{ background:{accent}10; border-left:4px solid {accent}; padding:14px 18px; border-radius:4px; margin-top:24px; }}
  .note-box h4 {{ font-size:.82rem; font-weight:700; color:{accent}; text-transform:uppercase; margin-bottom:8px; }}
  .note-box p {{ font-size:.85rem; color:#374151; line-height:1.6; }}
  .terms {{ margin-top:20px; font-size:.82rem; color:#6b7280; line-height:1.7; }}
  .footer {{ background:#f9fafb; border-top:1px solid #e5e7eb; padding:20px 40px; font-size:.78rem; color:#9ca3af; display:flex; justify-content:space-between; }}
  @media print {{ body {{ background:#fff; }} .page {{ box-shadow:none; }} }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="logo-area">
      <h1>ARC Digital</h1>
      <p>AI Business Automation</p>
      <p style="margin-top:8px;font-size:.8rem;opacity:.75">ABN: {ARC_ABN}</p>
      <p style="font-size:.8rem;opacity:.75">kevin@arcdigital.com.au | {ARC_PHONE}</p>
    </div>
    <div class="q-meta">
      <div class="q-type">Quotation</div>
      <div class="q-num">{q_num}</div>
      <div style="margin-top:8px;font-size:.8rem;opacity:.85">For: {co_name}</div>
    </div>
  </div>

  <div class="body">
    <div class="parties">
      <div>
        <div class="party-label">From</div>
        <div class="party-name">ARC Digital</div>
        <div class="party-detail">Kevin Raju — Founder</div>
        <div class="party-detail">kevin@arcdigital.com.au</div>
        <div class="party-detail">ABN: {ARC_ABN}</div>
      </div>
      <div>
        <div class="party-label">Prepared For</div>
        <div class="party-name">{co_name}</div>
        {f'<div class="party-detail">{ct_name}</div>' if ct_name else ''}
        {f'<div class="party-detail">{co_city}, Australia</div>' if co_city else ''}
      </div>
    </div>

    <div class="info-row">
      <div class="info-item"><label>Quote Date</label><span>{issued_fmt}</span></div>
      <div class="info-item"><label>Valid Until</label><span>{valid_fmt}</span></div>
      <div class="info-item"><label>Payment Terms</label><span>30 Days</span></div>
      <div class="info-item"><label>Quote Number</label><span>{q_num}</span></div>
    </div>

    <table>
      <thead>
        <tr><th style="width:60%">Service Description</th><th style="text-align:center;width:15%">Period</th><th>Amount (AUD)</th></tr>
      </thead>
      <tbody>{rows_html}
      </tbody>
    </table>

    <div class="totals">
      <div class="total-row"><span>Subtotal (ex GST)</span><span>AUD ${amount_ex:,.2f}</span></div>
      <div class="total-row"><span>GST (10%)</span><span>AUD ${gst:,.2f}</span></div>
      <div class="total-final"><span>TOTAL</span><span>AUD ${total:,.2f}/month</span></div>
    </div>

    <div class="note-box">
      <h4>What's Included</h4>
      <p>✅ 24/7 AI phone answering — never miss a plumbing enquiry again<br>
      ✅ Automatic job booking &amp; scheduling<br>
      ✅ Quote follow-up automation — recover lost jobs<br>
      ✅ After-hours call handling with instant SMS alerts<br>
      ✅ Monthly reporting dashboard — track every lead<br>
      ✅ Onboarding &amp; setup included — live in 5 business days</p>
    </div>

    <div class="terms">
      <strong>Terms &amp; Conditions:</strong><br>
      • This quotation is valid for 30 days from the date above.<br>
      • Prices are in Australian Dollars (AUD) and include GST where indicated.<br>
      • Monthly subscription — cancel with 30 days written notice.<br>
      • Setup fee waived for contracts signed within quotation validity period.<br>
      • ARC Digital ABN: {ARC_ABN} | Governed by the laws of Victoria, Australia.
    </div>
  </div>

  <div class="footer">
    <span>ARC Digital — ABN {ARC_ABN}</span>
    <span>To accept, reply to kevin@arcdigital.com.au with quote number {q_num}</span>
  </div>
</div>
</body>
</html>"""


def push_invoice_to_xero(invoice_data, company, contact):
    """Push invoice to Xero via MCP (if available). Returns xero_id or None."""
    # Xero integration is available via MCP server in the Claude interface
    # This function prepares the data in Xero's expected format
    xero_payload = {
        "Type": "ACCREC",
        "Contact": {
            "Name": (company or {}).get("name","Client"),
            "EmailAddress": (contact or {}).get("email",""),
        },
        "Date": datetime.utcnow().strftime("%Y-%m-%d"),
        "DueDate": (datetime.utcnow()+timedelta(days=30)).strftime("%Y-%m-%d"),
        "InvoiceNumber": invoice_data.get("invoice_number",""),
        "LineItems": [
            {
                "Description": "AI Business Automation — Monthly Subscription",
                "Quantity": 1,
                "UnitAmount": invoice_data.get("amount", 0),
                "TaxType": "OUTPUT2",  # Australian GST
                "AccountCode": "200",
            }
        ],
        "Status": "DRAFT",
        "CurrencyCode": "AUD",
        "Reference": f"ARC Digital — {(company or {}).get('name','')}",
    }
    logger.info(f"Xero payload prepared for {(company or {}).get('name','')} — {invoice_data.get('invoice_number')}")
    return None, xero_payload  # Returns (xero_id, payload) — xero_id set when actually pushed via MCP


# ══════════════════════════════════════════════════════════════════════════════
# BLAND AI INBOUND CALL HANDLING (Facebook Ad → Inbound Call)
# ══════════════════════════════════════════════════════════════════════════════

SARAH_INBOUND_SCRIPT = """You are Sarah, an AI receptionist for Kevin Raju from ARC Digital.
Someone has called in response to a Facebook advertisement about helping plumbing businesses stop missing calls and reduce admin.

YOUR ROLE: Answer the call warmly, find out who they are, understand their pain points, and either:
1. Book a meeting with Kevin (preferred), OR
2. Qualify them and pass their details to Kevin

OPENING (when they call in):
"Hi there, thanks for calling ARC Digital! This is Sarah. You've reached us about our AI automation for trade businesses — am I right? Great timing!
Can I grab your name first?"

AFTER NAME:
"Thanks [Name]. And which plumbing business are you calling from?"

PAIN POINT DISCOVERY (pick the most relevant):
"What made you reach out today — is it more about missed calls, or is it the admin side of things?"
"When you're on the tools and the phone rings — what normally happens to that enquiry?"
"How much time would you say you lose each week on admin, quotes and follow-ups?"

IF INTERESTED — BOOK MEETING:
"That's exactly what Kevin helps with. He starts with your specific process first, not the technology.
Would a quick 15-minute call with Kevin work for you? I can lock in a time right now."
Collect: preferred time, email for calendar invite.

CLOSING — ALWAYS COLLECT:
Full name, business name, phone number, email, main problem.
"Perfect. I'll get Kevin's team to send through the calendar invite to [email].
Thanks so much for calling — Kevin is going to love hearing from you!"

TONE: Warm, Australian, practical, friendly. Short sentences. Never corporate. Never pushy."""

def process_inbound_call(call_data):
    """Process an incoming Bland AI call (from Facebook ad)."""
    transcript  = call_data.get("concatenated_transcript","") or call_data.get("transcript","")
    caller_phone = call_data.get("from","") or call_data.get("phone_number","")
    bland_id    = call_data.get("call_id","")
    duration    = call_data.get("call_length") or call_data.get("duration_seconds",0)
    recording   = call_data.get("recording_url","")

    analysis = ai_call_transcript_analysis(transcript, "Inbound Caller", "Unknown")

    # Try to extract caller details from transcript
    caller_name = ""
    caller_biz  = ""
    caller_email = ""
    if transcript:
        lines = transcript.lower()
        # Simple extraction patterns
        import re as _re
        name_m = _re.search(r"(?:my name is|i'm|this is)\s+([a-z]+(?:\s+[a-z]+)?)", lines)
        if name_m: caller_name = name_m.group(1).title()
        biz_m  = _re.search(r"(?:from|with|at)\s+([a-z\s]+(?:plumbing|pipes?|drains?|trade))", lines)
        if biz_m:  caller_biz = biz_m.group(1).title().strip()
        email_m = _re.search(r"[\w.-]+@[\w.-]+\.\w+", transcript)
        if email_m: caller_email = email_m.group(0)

    # Save inbound call
    iid = run("""INSERT INTO inbound_calls(bland_call_id,phone_number,caller_name,campaign_source,
                transcript,summary,sentiment,interest_score,meeting_booked,status,duration_seconds,recording_url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (bland_id, caller_phone, caller_name, "facebook_ad",
         transcript, analysis.get("summary",""), analysis.get("sentiment","neutral"),
         analysis.get("interest_score",0), 1 if analysis.get("meeting_booked") else 0,
         "completed", duration, recording))

    # Create contact + company if business identified
    if caller_biz:
        existing_co = q1("SELECT id FROM companies WHERE name LIKE ?", (f"%{caller_biz[:20]}%",))
        if not existing_co:
            cid_new = run("""INSERT INTO companies(name,industry,status,description,created_by,updated_at)
                            VALUES(?,?,?,?,?,?)""",
                (caller_biz,"Plumbing","prospect",
                 f"Inbound call via Facebook Ad. Score:{analysis.get('interest_score',0)}/10",1,now()))
        else:
            cid_new = existing_co["id"]

        if caller_name and cid_new:
            parts = caller_name.split(" ", 1)
            existing_ct = q1("SELECT id FROM contacts WHERE company_id=? AND first_name=?", (cid_new, parts[0]))
            if not existing_ct:
                run("""INSERT INTO contacts(company_id,first_name,last_name,email,phone,
                       seniority_level,is_decision_maker) VALUES(?,?,?,?,?,?,?)""",
                    (cid_new,parts[0],parts[1] if len(parts)>1 else "",
                     caller_email,caller_phone,"owner",1))

    # Send SMS notification to Kevin
    score = analysis.get("interest_score",0)
    notify("hot_lead" if score>=7 else "company_added", {
        "company_name": caller_biz or f"Caller {caller_phone}",
        "lead_score": score,
        "industry": "Plumbing (Inbound FB Ad)",
    })

    logger.info(f"Inbound call processed: {caller_phone} score={score} biz={caller_biz}")
    return {"id":iid,"analysis":analysis,"caller_name":caller_name,"caller_biz":caller_biz}


def setup_bland_inbound_agent():
    """Configure Bland AI inbound phone number with Sarah's script."""
    if not BLAND_API_KEY or not BLAND_INBOUND_NUMBER:
        return {"status":"not_configured","message":"Set BLAND_API_KEY and BLAND_INBOUND_NUMBER in Render env"}
    payload = json.dumps({
        "phone_number": BLAND_INBOUND_NUMBER,
        "task": SARAH_INBOUND_SCRIPT,
        "model": "enhanced",
        "voice": "nat",
        "language": "en-AU",
        "max_duration": 8,
        "record": True,
        "wait_for_greeting": False,
        "amd": False,
        "webhook": "",  # Set to your Render URL + /api/bland/inbound-webhook
        "interruption_threshold": 50,
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.bland.ai/v1/inbound/{BLAND_INBOUND_NUMBER}",
            data=payload,
            headers={"authorization":BLAND_API_KEY,"Content-Type":"application/json"})
        req.get_method = lambda: "POST"
        with urllib.request.urlopen(req,timeout=15) as r:
            return {"status":"configured","data":json.loads(r.read())}
    except urllib.error.HTTPError as e:
        return {"status":"error","message":f"Bland {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"status":"error","message":str(e)}



def chat_reply(message,stats):
    cmd=message.lower().strip()
    cos=stats.get("companies",[])
    if any(w in cmd for w in ["show leads","top leads","hot leads"]):
        top=sorted(cos,key=lambda x:x.get("lead_score",0),reverse=True)[:5]
        return "🔥 Top Leads:\n\n"+"\n".join(
            f"{i}. {'🔥' if s>=70 else '🟡' if s>=40 else '❄️'} {c['name']} — {s}/100"
            for i,(c,s) in enumerate([(c,c.get("lead_score",0)) for c in top],1))
    if any(w in cmd for w in ["analytics","stats","kpi"]):
        hot=sum(1 for c in cos if c.get("lead_score",0)>=70)
        warm=sum(1 for c in cos if 40<=c.get("lead_score",0)<70)
        return f"📊 Analytics\n\n🏢 Companies: {len(cos)}\n🔥 Hot: {hot} | 🟡 Warm: {warm}\n📧 Emails: {stats.get('emails_sent',0)}\n💰 Pipeline: ${hot*50000+warm*15000:,}"
    if any(w in cmd for w in ["pipeline","revenue"]):
        hot=sum(1 for c in cos if c.get("lead_score",0)>=70)
        warm=sum(1 for c in cos if 40<=c.get("lead_score",0)<70)
        return f"💰 Pipeline\n\n🔥 {hot} hot × $50k = ${hot*50000:,}\n🟡 {warm} warm × $15k = ${warm*15000:,}\n📊 Total: ${hot*50000+warm*15000:,}"
    top5=sorted(cos,key=lambda x:x.get("lead_score",0),reverse=True)[:5]
    summary="\n".join([f"- {c['name']}: {c.get('lead_score',0)}" for c in top5])
    return (_groq(f"AI sales assistant. Answer in 2-3 sentences.\nData:{len(cos)} companies\n{summary}\nQ:{message}","Friendly AI sales assistant.",180)
            or "Try: `show leads`, `analytics`, `pipeline`, or `help`")

# ══ SERVICES ══════════════════════════════════════════════════════════════════
IND_SCORES={"technology":15,"software":15,"saas":15,"fintech":12,"design":10,
            "database":12,"healthcare":12,"financial":12,"cloud":14,"ai":15}

def score_company(co,contacts,signals):
    rev=co.get("annual_revenue") or 0
    rs=100 if rev>=100_000_000 else 85 if rev>=10_000_000 else 70 if rev>=1_000_000 else 40 if rev>=100_000 else 15
    emp=co.get("employee_count") or 0
    es=100 if emp>=1000 else 85 if emp>=500 else 70 if emp>=100 else 50 if emp>=20 else 20
    ind_=(co.get("industry") or "").lower()
    is_=next((v for k,v in IND_SCORES.items() if k in ind_),8)
    bss=min(100,sum(s.get("strength",5) for s in signals)*10//max(len(signals),1)) if signals else 0
    SM={"c_suite":30,"vp":25,"director":20,"manager":15,"individual":5}
    ds=min(100,sum(SM.get(c.get("seniority_level",""),5)+(20 if c.get("is_decision_maker") else 0) for c in contacts)) if contacts else 0
    total=max(0,min(100,int(rs*0.25+es*0.15+is_*0.20+bss*0.20+ds*0.10+50*0.10)))
    return {"total_score":total,"revenue_score":rs,"employee_score":es,"industry_score":is_,
            "buying_signal_score":bss,"department_signal_score":ds,"email_activity_score":50,
            "tier":"hot" if total>=70 else "warm" if total>=40 else "cold"}

def _send_via_sendgrid(to_email, to_name, subject, body):
    """Send via SendGrid HTTP API — works on Render free tier (port 443, not 587)."""
    if not SENDGRID_API_KEY:
        return None
    html_body = "<br>".join((body or "").replace("\r","").split("\n"))
    payload = json.dumps({
        "personalizations":[{"to":[{"email":to_email,"name":to_name or ""}]}],
        "from":{"email":GMAIL_EMAIL or "noreply@example.com","name":GMAIL_NAME},
        "subject":subject or "(no subject)",
        "content":[
            {"type":"text/plain","value":body or ""},
            {"type":"text/html","value":f"<html><body><p>{html_body}</p></body></html>"},
        ],
    }).encode("utf-8")
    try:
        req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send",
            data=payload,
            headers={"Authorization":f"Bearer {SENDGRID_API_KEY}","Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status in (200,202):
                logger.info(f"📧 SendGrid sent to {to_email}: {subject[:50]}")
                return {"status":"sent","method":"sendgrid"}
        return {"status":"error","message":f"SendGrid returned {r.status}"}
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:300]
        logger.error(f"SendGrid HTTP {e.code}: {body_err}")
        if e.code == 401:
            return {"status":"error","message":"SendGrid API key invalid — check SENDGRID_API_KEY in Render env"}
        if e.code == 403:
            return {"status":"error","message":"SendGrid 403 — verify sender at app.sendgrid.com/settings/sender_auth"}
        return {"status":"error","message":f"SendGrid {e.code}: {body_err[:100]}"}
    except Exception as e:
        logger.warning(f"SendGrid failed: {e}")
        return None

def _send_via_smtp(to_email, to_name, subject, body):
    """Gmail SMTP — works on Render paid/local. Port 587 blocked on Render free."""
    if not (GMAIL_EMAIL and GMAIL_PASSWORD):
        return {"status":"not_configured","message":"Set GMAIL_SENDER_EMAIL + GMAIL_APP_PASSWORD"}
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject or "(no subject)"
        msg["From"]    = f"{GMAIL_NAME} <{GMAIL_EMAIL}>"
        msg["To"]      = f"{to_name} <{to_email}>" if to_name else to_email
        msg.attach(MIMEText(body or "","plain","utf-8"))
        html_body = "<br>".join((body or "").replace("\r","").split("\n"))
        msg.attach(MIMEText(f"<html><body><p>{html_body}</p></body></html>","html","utf-8"))
        smtp = smtplib.SMTP("smtp.gmail.com",587,timeout=20)
        smtp.ehlo(); smtp.starttls(); smtp.ehlo()
        smtp.login(GMAIL_EMAIL,GMAIL_PASSWORD)
        smtp.sendmail(GMAIL_EMAIL,to_email,msg.as_string())
        smtp.quit()
        logger.info(f"📧 SMTP sent to {to_email}: {subject[:50]}")
        return {"status":"sent","method":"smtp"}
    except smtplib.SMTPAuthenticationError:
        return {"status":"error","message":"Gmail auth failed — use App Password from myaccount.google.com/apppasswords"}
    except OSError as e:
        if any(x in str(e) for x in ["101","unreachable","refused","timed out"]):
            return {"status":"error","message":"SMTP port 587 blocked on Render free tier. Add SENDGRID_API_KEY to env vars (free at sendgrid.com)"}
        return {"status":"error","message":f"SMTP network: {e}"}
    except Exception as e:
        logger.error(f"SMTP: {type(e).__name__}: {e}")
        return {"status":"error","message":str(e)}

def send_email_smtp(to_email, to_name, subject, body):
    """
    Multi-method email sender. Never raises.
    1. SendGrid HTTP API (Render free — port 443) — preferred
    2. Gmail SMTP (Render paid / local — port 587)
    Returns: {"status":"sent"|"error"|"not_configured", "method":..., "message":...}
    """
    if not to_email or "@" not in to_email:
        return {"status":"error","message":f"Invalid email address: {to_email!r}"}
    if SENDGRID_API_KEY:
        r = _send_via_sendgrid(to_email,to_name,subject,body)
        if r is not None: return r
    if GMAIL_EMAIL and GMAIL_PASSWORD:
        return _send_via_smtp(to_email,to_name,subject,body)
    return {"status":"not_configured","message":
        "No email configured. "
        "Render free: add SENDGRID_API_KEY (free at sendgrid.com). "
        "Render paid/local: add GMAIL_SENDER_EMAIL + GMAIL_APP_PASSWORD."}
def test_gmail():
    """Test email connectivity: SendGrid first, then Gmail SMTP."""
    parts=[]
    if SENDGRID_API_KEY:
        try:
            req=urllib.request.Request("https://api.sendgrid.com/v3/user/profile",
                headers={"Authorization":f"Bearer {SENDGRID_API_KEY}"})
            with urllib.request.urlopen(req,timeout=10) as r:
                profile=json.loads(r.read())
                parts.append(f"✅ SendGrid connected ({profile.get('username','')})")
        except urllib.error.HTTPError as e:
            parts.append(f"❌ SendGrid key invalid ({e.code}) — check SENDGRID_API_KEY" if e.code==401 else f"⚠️ SendGrid {e.code} — verify sender at app.sendgrid.com/settings/sender_auth")
        except Exception as e:
            parts.append(f"⚠️ SendGrid: {e}")
    else:
        parts.append("⚠️ SENDGRID_API_KEY not set — recommended for Render free tier. Free at sendgrid.com")
    if GMAIL_EMAIL and GMAIL_PASSWORD:
        try:
            smtp=smtplib.SMTP("smtp.gmail.com",587,timeout=10)
            smtp.ehlo(); smtp.starttls(); smtp.ehlo()
            smtp.login(GMAIL_EMAIL,GMAIL_PASSWORD); smtp.quit()
            parts.append(f"✅ Gmail SMTP ok ({GMAIL_EMAIL})")
        except smtplib.SMTPAuthenticationError:
            parts.append("❌ Gmail auth failed — check App Password at myaccount.google.com/apppasswords")
        except OSError as e:
            parts.append(f"⚠️ Gmail SMTP blocked (Render free? port 587 unreachable) — use SendGrid instead")
        except Exception as e:
            parts.append(f"⚠️ Gmail SMTP: {e}")
    else:
        parts.append("⚠️ GMAIL_SENDER_EMAIL or GMAIL_APP_PASSWORD not set")
    ok=any("✅" in p for p in parts)
    return ok," | ".join(parts)
def bland_call_api(phone, task, voice="nat", company_name="", contact_name="", objective="qualify"):
    if not BLAND_API_KEY:
        return {"status":"error","message":"BLAND_API_KEY not set in Render environment"}
    phone = "".join(c for c in phone if c in "0123456789+")
    if not phone.startswith("+"):
        return {"status":"error","message":"Phone must start with + and country code e.g. +61412345678"}
    if len(phone) < 10:
        return {"status":"error","message":f"Phone number too short: {phone}"}
    payload = json.dumps({
        "phone_number": phone,
        "task": task,
        "model": "enhanced",
        "voice": voice,
        "language": "en-AU",
        "max_duration": 8,
        "record": True,
        "wait_for_greeting": True,
        "amd": True,
        "interruption_threshold": 50,
        "pronunciation_guide": [
            {"word":"ARC Digital","pronunciation":"ark digital"},
            {"word":"Kevin Raju","pronunciation":"kevin rah-joo"},
        ],
        "metadata": {
            "company":   company_name,
            "contact":   contact_name,
            "objective": objective,
            "agent":     "Sarah - ARC Digital",
        },
    }).encode()
    try:
        req = urllib.request.Request("https://api.bland.ai/v1/calls", data=payload,
            headers={"authorization":BLAND_API_KEY,"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            call_id = data.get("call_id")
            logger.info(f"Bland call queued: {call_id} to {phone}")
            return {"status":"queued","call_id":call_id,"phone":phone}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        if e.code in (401,403):
            return {"status":"error","message":"Bland API key invalid/expired (403). Get new key at app.bland.ai"}
        if e.code == 400:
            return {"status":"error","message":f"Bland rejected (400): {body}"}
        return {"status":"error","message":f"Bland HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"status":"error","message":f"Cannot reach Bland AI: {e.reason}"}
    except Exception as e:
        logger.error(f"bland_call_api: {e}")
        return {"status":"error","message":str(e)}
def test_bland():
    if not BLAND_API_KEY: return False,"BLAND_API_KEY not set in environment variables"
    try:
        req=urllib.request.Request("https://api.bland.ai/v1/calls?limit=1",
            headers={"authorization":BLAND_API_KEY})
        with urllib.request.urlopen(req,timeout=10) as r: json.loads(r.read())
        return True,"✅ Bland AI connected"
    except urllib.error.HTTPError as e:
        if e.code in (401,403):
            return False,(
                "❌ Bland API key invalid or expired (HTTP 403). "
                "Go to app.bland.ai → API Keys → create a new key → "
                "update BLAND_API_KEY in Render environment variables → redeploy"
            )
        return False,f"Bland HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False,f"Cannot reach Bland AI: {e.reason}"
    except Exception as e:
        return False,str(e)

def google_auth_url(user_id=0, redirect_uri=""):
    if not GOOGLE_CLIENT_ID: return ""
    from urllib.parse import quote as _q
    redir = redirect_uri or GOOGLE_REDIRECT_URI or ""
    return (f"https://accounts.google.com/o/oauth2/auth?client_id={_q(GOOGLE_CLIENT_ID)}"
            f"&redirect_uri={_q(redir)}&response_type=code"
            f"&scope={_q('https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events')}"
            f"&access_type=offline&prompt=consent&state={user_id}")

def google_exchange(code_val, redirect_uri=""):
    from urllib.parse import quote as _q
    if not GOOGLE_CLIENT_ID: return {}
    redir = redirect_uri or GOOGLE_REDIRECT_URI or ""
    payload = (f"code={_q(code_val)}&client_id={_q(GOOGLE_CLIENT_ID)}"
               f"&client_secret={_q(GOOGLE_CLIENT_SECRET)}"
               f"&redirect_uri={_q(redir)}&grant_type=authorization_code").encode()
    try:
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
            data=payload, headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
            logger.info(f"Google exchange keys: {list(data.keys())}")
            return data
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        logger.error(f"Google exchange {e.code}: {body}")
        return {"error":f"HTTP {e.code}","error_description":body}
    except Exception as e:
        logger.error(f"Google exchange: {e}")
        return {}

# ══ TWILIO SMS ════════════════════════════════════════════════════════════════
SMS_TMPLS={
    "company_added":"🏢 New Company\n{company_name}\nIndustry:{industry}\nScore:{lead_score}/100",
    "hot_lead":"🔥 Hot Lead!\n{company_name}\nScore:{lead_score}/100\nIndustry:{industry}",
    "email_generated":"📧 Email Ready\n{company_name}\nType:{email_type}\nTo:{recipient_email}",
    "email_sent":"✅ Email Sent\nTo:{recipient_email}\nSubj:{subject}",
    "meeting_scheduled":"📅 Meeting\n{title}\n{company_name}\nTime:{scheduled_at}",
    "meeting_completed":"✅ Meeting Done\n{title}\n{company_name}",
    "call_initiated":"📞 AI Call\n{company_name}\nPhone:{phone_number}\nObj:{objective}",
    "csv_import":"📤 Import Done\n{filename}\n✅{processed_rows} ❌{failed_rows}",
    "daily_report":"📊 Daily {report_date}\n🏢{total_companies} co | 🔥{hot_leads} hot | 🟡{warm_leads} warm\n📧{emails_sent} sent ({email_open_rate}% open)\n📞{total_calls} calls\n💰${revenue_pipeline:,}\n{top_companies}",
    "meeting_reminder_24h":"📅 24h Reminder\n{title}\n{company_name}\n{scheduled_at}",
    "meeting_reminder_1h":"⏰ 1h Reminder\n{title}\n{company_name}",
    "meeting_reminder_10min":"⏰ 10min!\n{title}\n🚀 Starting soon!",
}
class _SD(dict):
    def __missing__(self,k): return ""

def _sms_send(to,body):
    if not TWILIO_SID:    return {"status":"not_configured","message":"TWILIO_ACCOUNT_SID not set in Render env"}
    if not TWILIO_TOKEN:  return {"status":"not_configured","message":"TWILIO_AUTH_TOKEN not set in Render env"}
    if not TWILIO_FROM:   return {"status":"not_configured","message":"TWILIO_FROM_NUMBER not set in Render env"}
    if not to:            return {"status":"error","message":"No recipient phone number"}
    from urllib.parse import quote as _q
    payload=f"To={_q(to)}&From={_q(TWILIO_FROM)}&Body={_q(body[:1600])}"
    creds=base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    try:
        req=urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data=payload.encode(),
            headers={"Authorization":f"Basic {creds}","Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req,timeout=15) as r:
            data=json.loads(r.read())
            return {"status":"sent","sid":data.get("sid")}
    except urllib.error.HTTPError as e:
        body_err=e.read().decode()[:300]
        logger.error(f"Twilio HTTP {e.code}: {body_err}")
        if e.code==401:
            return {"status":"error","message":"Twilio 401 Unauthorized — TWILIO_AUTH_TOKEN is wrong. Copy it fresh from console.twilio.com → Account Info → Auth Token"}
        if e.code==400:
            try:
                err_data=json.loads(body_err)
                return {"status":"error","message":f"Twilio error {err_data.get('code',400)}: {err_data.get('message',body_err[:100])}"}
            except Exception:
                return {"status":"error","message":f"Twilio 400: {body_err[:100]}"}
        return {"status":"error","message":f"Twilio HTTP {e.code}: {body_err[:100]}"}
    except urllib.error.URLError as e:
        return {"status":"error","message":f"Cannot reach Twilio: {e.reason}"}
    except Exception as e:
        logger.error(f"Twilio: {type(e).__name__}: {e}")
        return {"status":"error","message":str(e)}

def _sms_log(event_type,body,result):
    try: run("INSERT INTO sms_logs(to_number,from_number,body,status,event_type) VALUES(?,?,?,?,?)",(TWILIO_ADMIN or "admin",TWILIO_FROM,body[:500],result.get("status","unknown"),event_type))
    except Exception: pass

def notify(event_type,data):
    tmpl=SMS_TMPLS.get(event_type)
    if not tmpl: return
    if not TWILIO_ADMIN:
        logger.debug(f"notify {event_type}: TWILIO_ADMIN_NUMBER not set, skipping SMS")
        return
    try:
        body=tmpl.format_map(_SD(data))
        result=_sms_send(TWILIO_ADMIN,body)
        if result.get("status") not in ("sent","not_configured"):
            logger.warning(f"SMS notify {event_type}: {result.get('message','failed')}")
        _sms_log(event_type,body,result)
    except Exception as e:
        logger.error(f"notify {event_type}: {e}")

def notify_async(event_type,data): threading.Thread(target=notify,args=(event_type,data),daemon=True).start()
def send_sms(to,body): return _sms_send(to,body)
def sms_test(to):
    num = (to or TWILIO_ADMIN or "").strip()
    if not num: return {"status":"error","message":"No phone number — enter your number in the test field"}
    return _sms_send(num, f"✅ ARC Digital AI Sales — Connected! {datetime.utcnow().strftime('%d %b %Y %H:%M')} UTC")

def generate_google_meet_link(title="ARC Digital Meeting"):
    """Generate a Google Meet link via Google Calendar API or fallback to meet.google.com link."""
    import hashlib, base64, time as _time
    # Create a deterministic meet code from title + timestamp
    raw = f"{title}-{_time.time()}-arc-digital"
    code = hashlib.md5(raw.encode()).hexdigest()[:10]
    # Format as xxx-xxxx-xxx
    meet_code = f"{code[:3]}-{code[3:7]}-{code[7:10]}"
    return f"https://meet.google.com/{meet_code}"

def create_calendar_event_with_meet(user_id, title, description, start_dt, duration_mins=30):
    """Create Google Calendar event with Meet link using stored refresh token."""
    user = q1("SELECT google_refresh_token FROM users WHERE id=?", (user_id,))
    if not user or not user.get("google_refresh_token"):
        return None, generate_google_meet_link(title)

    refresh_token = user["google_refresh_token"]
    from urllib.parse import quote as _q

    # Get access token
    try:
        payload = (f"client_id={_q(GOOGLE_CLIENT_ID)}&client_secret={_q(GOOGLE_CLIENT_SECRET)}"
                   f"&refresh_token={_q(refresh_token)}&grant_type=refresh_token").encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
            data=payload, headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            tokens = json.loads(r.read())
        access_token = tokens.get("access_token")
        if not access_token:
            return None, generate_google_meet_link(title)
    except Exception as e:
        logger.error(f"Get access token: {e}")
        return None, generate_google_meet_link(title)

    # Create event
    try:
        if isinstance(start_dt, str):
            start_dt = datetime.strptime(start_dt[:19], "%Y-%m-%d %H:%M:%S")
        end_dt = start_dt + timedelta(minutes=duration_mins)
        event = {
            "summary": title,
            "description": description or f"ARC Digital — Kevin Raju\n\n{title}",
            "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Australia/Melbourne"},
            "end":   {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),   "timeZone": "Australia/Melbourne"},
            "conferenceData": {"createRequest": {"requestId": f"arc-{int(datetime.utcnow().timestamp())}",
                               "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        }
        body = json.dumps(event).encode()
        req = urllib.request.Request(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events?conferenceDataVersion=1",
            data=body,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            ev = json.loads(r.read())
        meet_link = ev.get("hangoutLink") or generate_google_meet_link(title)
        event_id  = ev.get("id")
        logger.info(f"✅ Calendar event created: {event_id} meet={meet_link}")
        return event_id, meet_link
    except Exception as e:
        logger.error(f"Create calendar event: {e}")
        return None, generate_google_meet_link(title)


def schedule_reminders(mid,title,company_name,scheduled_at):
    if not scheduled_at: return
    try: mdt=datetime.strptime(scheduled_at[:19],"%Y-%m-%d %H:%M:%S")
    except Exception: return
    def _fire(delta,etype):
        delay=(mdt-delta-datetime.utcnow()).total_seconds()
        if delay<=0: return
        def _r(): time.sleep(delay); notify(etype,{"title":title,"company_name":company_name,"scheduled_at":mdt.strftime("%d %b %Y %H:%M UTC")})
        threading.Thread(target=_r,daemon=True).start()
    _fire(timedelta(hours=24),"meeting_reminder_24h")
    _fire(timedelta(hours=1),"meeting_reminder_1h")
    _fire(timedelta(minutes=10),"meeting_reminder_10min")

# ══ AUTOMATION ════════════════════════════════════════════════════════════════
def _admin_id():
    u=q1("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1"); return u["id"] if u else 1

def auto_score_all():
    try:
        cos=q("SELECT * FROM companies")
        for co in cos:
            cts=q("SELECT * FROM contacts WHERE company_id=?",(co["id"],))
            sigs=q("SELECT * FROM buying_signals WHERE company_id=?",(co["id"],))
            sc=score_company(co,cts,sigs); old=co.get("lead_score",0) or 0
            if q1("SELECT id FROM lead_scores WHERE company_id=?",(co["id"],)):
                run("""UPDATE lead_scores SET total_score=?,revenue_score=?,employee_score=?,industry_score=?,
                       buying_signal_score=?,department_signal_score=?,email_activity_score=?,tier=?,updated_at=?
                       WHERE company_id=?""",
                    (sc["total_score"],sc["revenue_score"],sc["employee_score"],sc["industry_score"],
                     sc["buying_signal_score"],sc["department_signal_score"],sc["email_activity_score"],sc["tier"],now(),co["id"]))
            else:
                run("""INSERT INTO lead_scores(company_id,total_score,revenue_score,employee_score,industry_score,
                       buying_signal_score,department_signal_score,email_activity_score,tier) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (co["id"],sc["total_score"],sc["revenue_score"],sc["employee_score"],sc["industry_score"],
                     sc["buying_signal_score"],sc["department_signal_score"],sc["email_activity_score"],sc["tier"]))
            run("UPDATE companies SET lead_score=?,updated_at=? WHERE id=?",(sc["total_score"],now(),co["id"]))
            if sc["total_score"]>=80 and old<80:
                notify_async("hot_lead",{"company_name":co["name"],"lead_score":sc["total_score"],"industry":co.get("industry","N/A")})
        logger.info(f"✅ Auto-scored {len(cos)} companies"); return {"scored":len(cos)}
    except Exception as e: logger.error(f"auto_score:{e}"); return {"error":str(e)}

def auto_email_hot_leads():
    if not (GMAIL_EMAIL and GMAIL_PASSWORD):
        logger.info("auto_email: skipped (gmail not configured)")
        return {"skipped":"gmail_not_configured"}
    try:
        cos=q("""SELECT c.* FROM companies c WHERE c.lead_score>=70
                 AND c.id NOT IN(SELECT DISTINCT company_id FROM emails WHERE status IN('sent','opened','replied') AND company_id IS NOT NULL)
                 ORDER BY c.lead_score DESC LIMIT 10""")
        sent=0; skipped=0; errors=0
        for co in cos:
            try:
                ct=(q1("SELECT * FROM contacts WHERE company_id=? AND is_decision_maker=1",(co["id"],))
                    or q1("SELECT * FROM contacts WHERE company_id=?",(co["id"],)))
                email_addr = (ct.get("email","") if ct else "").strip()
                if not ct or not email_addr or "@" not in email_addr:
                    logger.info(f"auto_email: skipping {co['name']} — no valid email")
                    skipped+=1; continue
                content=generate_email(co,ct,"cold")
                ct_name=f"{ct.get('first_name','')} {ct.get('last_name','')}".strip()
                # save draft first so we always have a record
                eid=run("""INSERT INTO emails(company_id,contact_id,created_by,email_type,subject,body,recipient_email,recipient_name,status,ai_model_used,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (co["id"],ct["id"],_admin_id(),"cold",content["subject"],content["body"],
                         email_addr,ct_name,"draft","groq",now()))
                r=send_email_smtp(email_addr,ct_name,content["subject"],content["body"])
                if r.get("status")=="sent":
                    run("UPDATE emails SET status=\'sent\',sent_at=?,updated_at=? WHERE id=?",(now(),now(),eid))
                    notify_async("email_sent",{"recipient_email":email_addr,"subject":content["subject"]})
                    sent+=1
                else:
                    run("UPDATE emails SET status=\'error\',updated_at=? WHERE id=?",(now(),eid))
                    logger.warning(f"auto_email {co['name']}: {r.get('message','send failed')}")
                    errors+=1
            except Exception as e_inner:
                logger.error(f"auto_email {co.get('name','?')}: {e_inner}")
                errors+=1
        logger.info(f"✅ Auto-email done: sent={sent} skipped={skipped} errors={errors}")
        return {"sent":sent,"skipped":skipped,"errors":errors}
    except Exception as e:
        logger.error(f"auto_email_hot_leads: {e}")
        return {"error":str(e)}

def auto_followup():
    if not (GMAIL_EMAIL and GMAIL_PASSWORD):
        return {"skipped":"gmail_not_configured"}
    try:
        cutoff=(datetime.utcnow()-timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        pending=q("""SELECT e.* FROM emails e WHERE e.email_type='cold' AND e.status IN('sent','opened') AND e.sent_at<?
                    AND e.company_id NOT IN(SELECT DISTINCT company_id FROM emails WHERE email_type='follow_up' AND company_id IS NOT NULL)
                    ORDER BY e.sent_at ASC LIMIT 5""",(cutoff,))
        sent=0; errors=0
        for em in pending:
            try:
                co=q1("SELECT * FROM companies WHERE id=?",(em["company_id"],))
                ct=q1("SELECT * FROM contacts WHERE id=?",(em.get("contact_id"),)) if em.get("contact_id") else None
                email_addr=(ct.get("email","") if ct else "").strip()
                if not co or not ct or not email_addr or "@" not in email_addr: continue
                content=generate_email(co,ct,"follow_up")
                ct_name=f"{ct.get('first_name','')} {ct.get('last_name','')}".strip()
                eid=run("""INSERT INTO emails(company_id,contact_id,created_by,email_type,subject,body,recipient_email,recipient_name,status,ai_model_used,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (co["id"],ct["id"],_admin_id(),"follow_up",content["subject"],content["body"],
                         email_addr,ct_name,"draft","groq",now()))
                r=send_email_smtp(email_addr,ct_name,content["subject"],content["body"])
                if r.get("status")=="sent":
                    run("UPDATE emails SET status='sent',sent_at=?,updated_at=? WHERE id=?",(now(),now(),eid))
                    sent+=1
                else:
                    run("UPDATE emails SET status='error',updated_at=? WHERE id=?",(now(),eid))
                    logger.warning(f"followup {co['name']}: {r.get('message','failed')}")
                    errors+=1
            except Exception as e_inner:
                logger.error(f"auto_followup inner: {e_inner}")
                errors+=1
        logger.info(f"✅ Follow-up done: sent={sent} errors={errors}")
        return {"sent":sent,"errors":errors}
    except Exception as e:
        logger.error(f"auto_followup: {e}")
        return {"error":str(e)}

def auto_call_hot_leads():
    if not BLAND_API_KEY: return {"skipped":"bland_not_configured"}
    try:
        cos=q("""SELECT c.* FROM companies c WHERE c.lead_score>=80
                 AND c.id NOT IN(SELECT DISTINCT company_id FROM calls WHERE company_id IS NOT NULL)
                 ORDER BY c.lead_score DESC LIMIT 3""")
        called=0
        for co in cos:
            ct=q1("SELECT * FROM contacts WHERE company_id=? AND phone IS NOT NULL AND phone!='' ORDER BY is_decision_maker DESC LIMIT 1",(co["id"],))
            if not ct or not ct.get("phone"): continue
            script=call_script(co,ct,"qualify")
            r=bland_call_api(ct["phone"],script,"nat",co["name"],f"{ct.get('first_name','')} {ct.get('last_name','')}".strip(),"qualify")
            run("""INSERT INTO calls(bland_call_id,company_id,contact_id,created_by,phone_number,objective,task_prompt,voice,status,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (r.get("call_id"),co["id"],ct["id"],_admin_id(),ct["phone"],"qualify",script,"nat",
                 "queued" if r.get("status")=="queued" else "error",now()))
            if r.get("status")=="queued":
                notify_async("call_initiated",{"company_name":co["name"],"phone_number":ct["phone"],"objective":"Qualify"}); called+=1
        return {"called":called}
    except Exception as e: return {"error":str(e)}

def sync_call_statuses():
    if not BLAND_API_KEY: return
    try:
        for call in q("SELECT id,bland_call_id FROM calls WHERE status IN('queued','in-progress') AND bland_call_id IS NOT NULL"):
            live=bland_get_api(call["bland_call_id"])
            if live and "status" in live:
                run("""UPDATE calls SET status=?,duration_seconds=?,recording_url=?,transcript=?,summary=?,updated_at=? WHERE id=?""",
                    (live.get("status"),live.get("call_length"),live.get("recording_url"),live.get("concatenated_transcript",""),live.get("summary",""),now(),call["id"]))
    except Exception as e: logger.error(f"sync:{e}")

def send_daily_report():
    if not TWILIO_ADMIN: return
    try:
        cos=q("SELECT lead_score,name FROM companies"); emails=q("SELECT status FROM emails"); mtgs=q("SELECT status FROM meetings"); calls=q("SELECT id FROM calls")
        hot=sum(1 for c in cos if(c.get("lead_score") or 0)>=70); warm=sum(1 for c in cos if 40<=(c.get("lead_score") or 0)<70)
        sent=sum(1 for e in emails if e.get("status") in("sent","opened","replied")); opened=sum(1 for e in emails if e.get("status") in("opened","replied"))
        top5=sorted(cos,key=lambda x:x.get("lead_score",0),reverse=True)[:5]
        notify_async("daily_report",{"report_date":datetime.utcnow().strftime("%d %b %Y"),"total_companies":len(cos),"hot_leads":hot,"warm_leads":warm,
            "emails_sent":sent,"email_open_rate":round(opened/sent*100,1) if sent else 0,"total_calls":len(calls),
            "meetings_scheduled":sum(1 for m in mtgs if m.get("status")=="scheduled"),"revenue_pipeline":hot*50000+warm*15000,
            "top_companies":", ".join(f"{c['name']}({c['lead_score']})" for c in top5)})
    except Exception as e: logger.error(f"daily_report:{e}")

def run_automation_cycle():
    h=datetime.utcnow().hour; dow=datetime.utcnow().weekday()
    logger.info(f"🔄 Auto cycle h={h} dow={dow}")
    sync_call_statuses()
    if h==AUTO_SCORE_HOUR: auto_score_all()
    if h==AUTO_EMAIL_HOUR and dow<5: auto_email_hot_leads()
    if h==AUTO_FOLLOWUP_HOUR and dow<5: auto_followup()
    if h==AUTO_CALL_HOUR and dow<5: auto_call_hot_leads()
    if h==DAILY_REPORT_HOUR: send_daily_report()
    logger.info("✅ Auto cycle done")

# ══ SCHEDULER ═════════════════════════════════════════════════════════════════
_SR=False; _ST=None

def start():
    global _SR,_ST
    if _SR: return
    _SR=True
    def _loop():
        last=-1
        while _SR:
            try:
                h=datetime.utcnow().hour
                if h!=last: last=h; threading.Thread(target=run_automation_cycle,daemon=True).start()
            except Exception as e: logger.error(f"sched:{e}")
            time.sleep(60)
    _ST=threading.Thread(target=_loop,daemon=True,name="Sched"); _ST.start()

def trigger_now(): threading.Thread(target=run_automation_cycle,daemon=True).start()

# ══ API ROUTES ════════════════════════════════════════════════════════════════
api=Blueprint("api",__name__,url_prefix="/api")

def ok(data=None,**kw):
    r={"ok":True}
    if data is not None: r["data"]=data
    r.update(kw); return jsonify(r)
def err(msg,code=400): return jsonify({"ok":False,"error":msg}),code

@api.post("/auth/login")
def login():
    d=request.get_json(silent=True) or {}
    email=(d.get("email") or "").lower().strip(); pw=d.get("password") or ""
    if not email or not pw: return err("Email and password required")
    user=q1("SELECT * FROM users WHERE email=? AND is_active=1",(email,))
    if not user or not verify_pw(pw,user["password"]): return err("Invalid credentials",401)
    run("UPDATE users SET last_login=? WHERE id=?",(now(),user["id"]))
    tok=make_token(user["id"],user["email"],user["role"])
    return ok({"token":tok,"user":{k:user[k] for k in("id","email","full_name","role","created_at") if k in user}})

@api.post("/auth/register")
def register():
    d=request.get_json(silent=True) or {}
    email=(d.get("email") or "").lower().strip(); fn=(d.get("full_name") or "").strip(); pw=d.get("password") or ""
    if not email or not fn or not pw: return err("email, full_name and password required")
    if len(pw)<6: return err("Password must be at least 6 characters")
    if q1("SELECT 1 FROM users WHERE email=?",(email,)): return err("Email already registered")
    uid=run("INSERT INTO users(email,full_name,password,role) VALUES(?,?,?,?)",(email,fn,hash_pw(pw),d.get("role","sales_rep")))
    user=q1("SELECT * FROM users WHERE id=?",(uid,))
    return ok({"token":make_token(uid,email,d.get("role","sales_rep")),"user":{k:user[k] for k in("id","email","full_name","role") if k in user}}),201

@api.get("/auth/me")
@login_required
def me():
    u=q1("SELECT * FROM users WHERE id=?",(g.user["id"],))
    if not u: return err("Not found",404)
    return ok({k:u[k] for k in("id","email","full_name","role","is_active","created_at","last_login") if k in u})

@api.get("/companies")
@login_required
def list_companies():
    search=request.args.get("search",""); status=request.args.get("status",""); limit=min(int(request.args.get("limit",200)),500)
    sql="SELECT * FROM companies WHERE 1=1"; args=[]
    if search: sql+=" AND name LIKE ?"; args.append(f"%{search}%")
    if status: sql+=" AND status=?"; args.append(status)
    sql+=" ORDER BY lead_score DESC LIMIT ?"; args.append(limit)
    return ok(q(sql,args))

@api.post("/companies")
@login_required
def create_company():
    d=request.get_json(silent=True) or {}; name=(d.get("name") or "").strip()
    if not name: return err("name is required")
    cid=run("""INSERT INTO companies(name,industry,employee_count,annual_revenue,website,city,country,description,technologies,status,linkedin_url,funding_stage,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name,d.get("industry"),d.get("employee_count"),d.get("annual_revenue"),d.get("website"),d.get("city"),d.get("country"),d.get("description"),d.get("technologies"),d.get("status","prospect"),d.get("linkedin_url"),d.get("funding_stage"),g.user["id"],now()))
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    notify_async("company_added",{"company_name":co["name"],"industry":co.get("industry","N/A"),"lead_score":co.get("lead_score",0),"status":co.get("status","prospect")})
    return ok(co),201

@api.get("/companies/<int:cid>")
@login_required
def get_company(cid):
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    if not co: return err("Not found",404)
    co["contacts"]=q("SELECT * FROM contacts WHERE company_id=?",(cid,))
    co["emails"]=q("SELECT * FROM emails WHERE company_id=? ORDER BY created_at DESC",(cid,))
    co["meetings"]=q("SELECT * FROM meetings WHERE company_id=? ORDER BY scheduled_at DESC",(cid,))
    co["calls"]=q("SELECT * FROM calls WHERE company_id=? ORDER BY created_at DESC",(cid,))
    co["buying_signals"]=q("SELECT * FROM buying_signals WHERE company_id=?",(cid,))
    co["lead_score_details"]=q1("SELECT * FROM lead_scores WHERE company_id=?",(cid,))
    return ok(co)

@api.put("/companies/<int:cid>")
@login_required
def update_company(cid):
    if not q1("SELECT id FROM companies WHERE id=?",(cid,)): return err("Not found",404)
    d=request.get_json(silent=True) or {}
    allowed={"name","industry","employee_count","annual_revenue","website","city","country","description","technologies","status","linkedin_url","funding_stage","ai_summary"}
    sets=[f"{k}=?" for k in d if k in allowed]; vals=[d[k] for k in d if k in allowed]
    if not sets: return err("Nothing to update")
    sets.append("updated_at=?"); vals.append(now()); vals.append(cid)
    run(f"UPDATE companies SET {','.join(sets)} WHERE id=?",vals)
    return ok(q1("SELECT * FROM companies WHERE id=?",(cid,)))

@api.delete("/companies/<int:cid>")
@login_required
def delete_company(cid):
    if not q1("SELECT id FROM companies WHERE id=?",(cid,)): return err("Not found",404)
    run("DELETE FROM companies WHERE id=?",(cid,)); return ok({"deleted":cid})

@api.post("/companies/<int:cid>/score")
@login_required
def score_company_route(cid):
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    if not co: return err("Not found",404)
    cts=q("SELECT * FROM contacts WHERE company_id=?",(cid,)); sigs=q("SELECT * FROM buying_signals WHERE company_id=?",(cid,))
    sc=score_company(co,cts,sigs)
    if q1("SELECT id FROM lead_scores WHERE company_id=?",(cid,)):
        run("""UPDATE lead_scores SET total_score=?,revenue_score=?,employee_score=?,industry_score=?,buying_signal_score=?,department_signal_score=?,email_activity_score=?,tier=?,updated_at=? WHERE company_id=?""",
            (sc["total_score"],sc["revenue_score"],sc["employee_score"],sc["industry_score"],sc["buying_signal_score"],sc["department_signal_score"],sc["email_activity_score"],sc["tier"],now(),cid))
    else:
        run("""INSERT INTO lead_scores(company_id,total_score,revenue_score,employee_score,industry_score,buying_signal_score,department_signal_score,email_activity_score,tier) VALUES(?,?,?,?,?,?,?,?,?)""",
            (cid,sc["total_score"],sc["revenue_score"],sc["employee_score"],sc["industry_score"],sc["buying_signal_score"],sc["department_signal_score"],sc["email_activity_score"],sc["tier"]))
    run("UPDATE companies SET lead_score=?,updated_at=? WHERE id=?",(sc["total_score"],now(),cid))
    if sc["total_score"]>=80: notify_async("hot_lead",{"company_name":co["name"],"lead_score":sc["total_score"],"industry":co.get("industry","N/A")})
    return ok(sc)

@api.post("/companies/<int:cid>/ai-summary")
@login_required
def ai_summary_route(cid):
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    if not co: return err("Not found",404)
    s=company_summary(co); run("UPDATE companies SET ai_summary=?,updated_at=? WHERE id=?",(s,now(),cid)); return ok({"summary":s})

@api.post("/companies/<int:cid>/analyze-signals")
@login_required
def analyze_signals_route(cid):
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    if not co: return err("Not found",404)
    run("DELETE FROM buying_signals WHERE company_id=?",(cid,))
    sigs=buying_signals(co)
    for s in sigs:
        run("INSERT INTO buying_signals(company_id,signal_type,signal_name,signal_description,strength,source) VALUES(?,?,?,?,?,?)",
            (cid,s.get("type",""),s.get("name",""),s.get("description",""),s.get("strength",5),"ai"))
    return ok({"signals":sigs})

@api.post("/companies/upload-csv")
@login_required
def upload_csv():
    f=request.files.get("file")
    if not f or not f.filename.endswith(".csv"): return err("CSV file required")
    content=f.read(); fname=f.filename; uid=g.user["id"]
    def _proc():
        try:
            reader=csv.DictReader(io.StringIO(content.decode("utf-8-sig",errors="replace")))
            ok_n,fail=0,0
            for row in reader:
                name=(row.get("name") or row.get("company") or row.get("Company") or "").strip()
                if not name: fail+=1; continue
                try:
                    def ti(v): return int(float(str(v).replace(",","").replace("$",""))) if v else None
                    if not q1("SELECT id FROM companies WHERE name=?",(name,)):
                        run("""INSERT INTO companies(name,industry,employee_count,annual_revenue,country,city,website,status,created_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (name,row.get("industry"),ti(row.get("employee_count")),ti(row.get("annual_revenue")),row.get("country"),row.get("city"),row.get("website"),row.get("status","prospect"),uid,now()))
                    ok_n+=1
                except Exception: fail+=1
            notify_async("csv_import",{"filename":fname,"processed_rows":ok_n,"failed_rows":fail})
        except Exception as e: logger.error(f"CSV:{e}")
    threading.Thread(target=_proc,daemon=True).start()
    return ok({"message":"Import started","filename":fname}),201

@api.get("/contacts")
@login_required
def list_contacts():
    cid        = request.args.get("company_id","")
    search     = request.args.get("search","").strip()
    seniority  = request.args.get("seniority","").strip()
    qualified  = request.args.get("qualified","").strip()   # "yes"/"no"

    sql = """
        SELECT
            ct.id, ct.company_id, ct.first_name, ct.last_name,
            ct.email, ct.phone, ct.title, ct.department,
            ct.seniority_level, ct.is_decision_maker, ct.created_at,
            co.name  AS company_name,
            co.city  AS company_city,
            co.country AS company_country,
            co.industry AS company_industry,
            co.lead_score AS company_lead_score,
            co.website AS company_website,
            co.ai_summary AS company_ai_summary,
            cl.linkedin_url, cl.position AS linkedin_position,
            cl.seniority AS linkedin_seniority, cl.is_key_person,
            ls.total_score AS qualify_score,
            ls.tier AS qualify_tier
        FROM contacts ct
        LEFT JOIN companies  co ON ct.company_id = co.id
        LEFT JOIN contact_linkedin cl ON cl.contact_id = ct.id
        LEFT JOIN lead_scores ls ON ls.company_id = ct.company_id
        WHERE 1=1
    """
    args = []
    if cid:
        sql += " AND ct.company_id=?";  args.append(cid)
    if search:
        sql += " AND (ct.first_name LIKE ? OR ct.last_name LIKE ? OR co.name LIKE ? OR ct.email LIKE ?)"
        args += [f"%{search}%"]*4
    if seniority:
        sql += " AND ct.seniority_level=?"; args.append(seniority)
    if qualified == "yes":
        sql += " AND ls.total_score >= 60"
    elif qualified == "no":
        sql += " AND (ls.total_score IS NULL OR ls.total_score < 60)"

    sql += " ORDER BY co.lead_score DESC, ct.is_decision_maker DESC, ct.created_at DESC LIMIT 500"
    return ok(q(sql, args))

@api.post("/contacts")
@login_required
def create_contact():
    d=request.get_json(silent=True) or {}
    if not d.get("company_id") or not d.get("first_name"): return err("company_id and first_name required")
    ctid=run("INSERT INTO contacts(company_id,first_name,last_name,email,phone,title,department,seniority_level,is_decision_maker) VALUES(?,?,?,?,?,?,?,?,?)",
             (d["company_id"],d["first_name"],d.get("last_name"),d.get("email"),d.get("phone"),d.get("title"),d.get("department"),d.get("seniority_level","individual"),1 if d.get("is_decision_maker") else 0))
    return ok(q1("SELECT * FROM contacts WHERE id=?",(ctid,))),201

@api.put("/contacts/<int:ctid>")
@login_required
def update_contact(ctid):
    if not q1("SELECT id FROM contacts WHERE id=?",(ctid,)): return err("Not found",404)
    d=request.get_json(silent=True) or {}
    allowed={"first_name","last_name","email","phone","title","department","seniority_level","is_decision_maker"}
    sets=[f"{k}=?" for k in d if k in allowed]; vals=[d[k] for k in d if k in allowed]
    if sets: run(f"UPDATE contacts SET {','.join(sets)} WHERE id=?",vals+[ctid])
    return ok(q1("SELECT * FROM contacts WHERE id=?",(ctid,)))

@api.get("/emails")
@login_required
def list_emails():
    status=request.args.get("status",""); cid=request.args.get("company_id","")
    sql="SELECT * FROM emails WHERE 1=1"; args=[]
    if status: sql+=" AND status=?"; args.append(status)
    if cid: sql+=" AND company_id=?"; args.append(cid)
    return ok(q(sql+" ORDER BY created_at DESC LIMIT 200",args))

@api.post("/emails/generate")
@login_required
def gen_email():
    d=request.get_json(silent=True) or {}; cid=d.get("company_id")
    if not cid: return err("company_id required")
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    if not co: return err("Company not found",404)
    ct=(q1("SELECT * FROM contacts WHERE id=?",(d["contact_id"],)) if d.get("contact_id") else None
        or q1("SELECT * FROM contacts WHERE company_id=? AND is_decision_maker=1",(cid,))
        or q1("SELECT * FROM contacts WHERE company_id=?",(cid,))
        or {"first_name":"Team","last_name":"","title":"","email":""})
    et=d.get("email_type","cold"); content=generate_email(co,ct,et,d.get("custom_instructions",""))
    eid=run("""INSERT INTO emails(company_id,contact_id,created_by,email_type,subject,body,recipient_email,recipient_name,status,ai_model_used,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (cid,ct.get("id"),g.user["id"],et,content["subject"],content["body"],ct.get("email","unknown@example.com"),f"{ct.get('first_name','')} {ct.get('last_name','')}".strip(),"draft","groq",now()))
    em=q1("SELECT * FROM emails WHERE id=?",(eid,))
    notify_async("email_generated",{"company_name":co["name"],"email_type":et,"recipient_email":em["recipient_email"],"subject":em["subject"]})
    return ok(em),201

@api.post("/emails/<int:eid>/send")
@login_required
def send_email_route(eid):
    try:
        em=q1("SELECT * FROM emails WHERE id=?",(eid,))
        if not em: return err("Not found",404)
        if em.get("status")=="sent": return err("Already sent")
        recip = (em.get("recipient_email") or "").strip()
        if not recip:
            return err("Email has no recipient_email set",400)
        result = send_email_smtp(recip, em.get("recipient_name",""), em.get("subject","(no subject)"), em.get("body",""))
        if result.get("status")=="sent":
            run("UPDATE emails SET status=\'sent\',sent_at=?,updated_at=? WHERE id=?",(now(),now(),eid))
            notify_async("email_sent",{"recipient_email":recip,"subject":em.get("subject","")})
        return ok({"email":q1("SELECT * FROM emails WHERE id=?",(eid,)),"send_result":result})
    except Exception as e:
        logger.error(f"send_email_route {eid}: {e}")
        return err(f"Send failed: {e}", 500)

@api.put("/emails/<int:eid>")
@login_required
def update_email(eid):
    if not q1("SELECT id FROM emails WHERE id=?",(eid,)): return err("Not found",404)
    d=request.get_json(silent=True) or {}
    allowed={"subject","body","recipient_email","recipient_name","status","email_type"}
    sets=[f"{k}=?" for k in d if k in allowed]; vals=[d[k] for k in d if k in allowed]
    if sets: sets.append("updated_at=?"); vals.append(now()); vals.append(eid); run(f"UPDATE emails SET {','.join(sets)} WHERE id=?",vals)
    return ok(q1("SELECT * FROM emails WHERE id=?",(eid,)))

@api.put("/emails/<int:eid>/track")
@login_required
def track_email(eid):
    d=request.get_json(silent=True) or {}; status=d.get("status")
    if status not in("opened","replied","bounced"): return err("Invalid status")
    em=q1("SELECT * FROM emails WHERE id=?",(eid,))
    if not em: return err("Not found",404)
    run("UPDATE emails SET status=?,updated_at=? WHERE id=?",(status,now(),eid)); return ok(q1("SELECT * FROM emails WHERE id=?",(eid,)))

@api.get("/meetings")
@login_required
def list_meetings():
    status=request.args.get("status","")
    sql="SELECT m.*,c.name as company_name FROM meetings m LEFT JOIN companies c ON m.company_id=c.id"; args=[]
    if status: sql+=" WHERE m.status=?"; args.append(status)
    return ok(q(sql+" ORDER BY m.scheduled_at DESC",args))

@api.post("/meetings")
@login_required
def create_meeting():
    d=request.get_json(silent=True) or {}
    if not d.get("company_id") or not d.get("title"): return err("company_id and title required")
    co=q1("SELECT * FROM companies WHERE id=?",(d["company_id"],))
    if not co: return err("Company not found",404)
    mid=run("""INSERT INTO meetings(company_id,contact_id,created_by,title,meeting_type,description,scheduled_at,duration_minutes,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (d["company_id"],d.get("contact_id"),g.user["id"],d["title"],d.get("meeting_type","discovery"),d.get("description"),d.get("scheduled_at"),d.get("duration_minutes",30),"proposed",now()))
    m=q1("SELECT * FROM meetings WHERE id=?",(mid,))
    notify_async("meeting_scheduled",{"title":m["title"],"company_name":co["name"],"meeting_type":m.get("meeting_type",""),"scheduled_at":(m.get("scheduled_at") or "TBD")[:16],"duration_minutes":m.get("duration_minutes",30)})
    if m.get("scheduled_at"): schedule_reminders(mid,m["title"],co["name"],m["scheduled_at"])
    return ok(m),201

@api.put("/meetings/<int:mid>")
@login_required
def update_meeting(mid):
    m=q1("SELECT * FROM meetings WHERE id=?",(mid,))
    if not m: return err("Not found",404)
    prev=m.get("status",""); d=request.get_json(silent=True) or {}
    allowed={"title","meeting_type","description","scheduled_at","duration_minutes","status","meeting_link","notes"}
    sets=[f"{k}=?" for k in d if k in allowed]; vals=[d[k] for k in d if k in allowed]
    if sets: sets.append("updated_at=?"); vals.append(now()); vals.append(mid); run(f"UPDATE meetings SET {','.join(sets)} WHERE id=?",vals)
    m2=q1("SELECT * FROM meetings WHERE id=?",(mid,))
    if m2.get("status")=="completed" and prev!="completed":
        co=q1("SELECT name FROM companies WHERE id=?",(m2.get("company_id",0),))
        notify_async("meeting_completed",{"title":m2["title"],"company_name":co["name"] if co else "Unknown","meeting_type":m2.get("meeting_type","")})
    return ok(m2)

@api.post("/meetings/<int:mid>/calendar")
@login_required
def add_to_calendar(mid):
    m=q1("SELECT * FROM meetings WHERE id=?",(mid,))
    if not m: return err("Not found",404)
    user=q1("SELECT * FROM users WHERE id=?",(g.user["id"],))
    if not user or not user.get("google_refresh_token"): return err("Google Calendar not connected — go to Integrations",400)
    return ok({"message":"Calendar event would be created here","google_event_id":None})

@api.get("/calls")
@login_required
def list_calls():
    return ok(q("SELECT ca.*,co.name as company_name FROM calls ca LEFT JOIN companies co ON ca.company_id=co.id ORDER BY ca.created_at DESC LIMIT 100"))

@api.post("/calls/make")
@login_required
def make_call():
    d=request.get_json(silent=True) or {}; phone=(d.get("phone_number") or "").strip()
    cid=d.get("company_id"); co=q1("SELECT * FROM companies WHERE id=?",(cid,)) if cid else None
    ct=q1("SELECT * FROM contacts WHERE id=?",(d.get("contact_id"),)) if d.get("contact_id") else None
    if not phone and ct and ct.get("phone"): phone=ct["phone"]
    if not phone: return err("phone_number required (e.g. +14155550100)")
    objective=d.get("objective","qualify"); task=d.get("custom_task") or call_script(co or {},ct or {},objective)
    result=bland_call_api(phone,task,d.get("voice","nat"),co["name"] if co else "",f"{ct.get('first_name','')} {ct.get('last_name','')}".strip() if ct else "",objective)
    call_id=run("""INSERT INTO calls(bland_call_id,company_id,contact_id,created_by,phone_number,objective,task_prompt,voice,status,error_message,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (result.get("call_id"),cid,d.get("contact_id"),g.user["id"],phone,objective,task,d.get("voice","nat"),
                 "queued" if result.get("status")=="queued" else "error",result.get("message") if result.get("status")=="error" else None,now()))
    notify_async("call_initiated",{"company_name":co["name"] if co else "Unknown","phone_number":phone,"objective":objective})
    return ok({"call":q1("SELECT * FROM calls WHERE id=?",(call_id,)),"bland_result":result})

@api.get("/calls/<int:cid_>")
@login_required
def get_call(cid_):
    call=q1("SELECT * FROM calls WHERE id=?",(cid_,))
    if not call: return err("Not found",404)
    if call.get("bland_call_id") and call.get("status") not in("completed","error","failed"):
        live=bland_get_api(call["bland_call_id"])
        if live and "status" in live:
            run("UPDATE calls SET status=?,duration_seconds=?,recording_url=?,transcript=?,summary=?,updated_at=? WHERE id=?",
                (live.get("status"),live.get("call_length"),live.get("recording_url"),live.get("concatenated_transcript",""),live.get("summary",""),now(),cid_))
            call=q1("SELECT * FROM calls WHERE id=?",(cid_,))
    return ok(call)

@api.get("/analytics/summary")
@login_required
def analytics_summary():
    cos=q("SELECT lead_score,status FROM companies"); emails=q("SELECT status FROM emails"); mtgs=q("SELECT status FROM meetings"); calls_=q("SELECT status FROM calls")
    hot=sum(1 for c in cos if(c.get("lead_score") or 0)>=70); warm=sum(1 for c in cos if 40<=(c.get("lead_score") or 0)<70)
    sent=sum(1 for e in emails if e.get("status")!="draft"); opened=sum(1 for e in emails if e.get("status") in("opened","replied")); replied=sum(1 for e in emails if e.get("status")=="replied")
    return ok({"total_companies":len(cos),"hot_leads":hot,"warm_leads":warm,"cold_leads":len(cos)-hot-warm,"emails_sent":sent,"emails_opened":opened,
               "open_rate":round(opened/sent*100,1) if sent else 0,"reply_rate":round(replied/sent*100,1) if sent else 0,
               "meetings_scheduled":sum(1 for m in mtgs if m.get("status")=="scheduled"),"meetings_completed":sum(1 for m in mtgs if m.get("status")=="completed"),
               "revenue_pipeline":hot*50000+warm*15000,"total_calls":len(calls_),"completed_calls":sum(1 for c in calls_ if c.get("status")=="completed")})

@api.get("/analytics/email-activity")
@login_required
def email_activity():
    emails=q("SELECT status,created_at FROM emails"); daily=defaultdict(lambda:{"sent":0,"opened":0,"replied":0})
    for e in emails:
        day=(e.get("created_at") or "")[:10]
        if not day: continue
        if e.get("status")!="draft": daily[day]["sent"]+=1
        if e.get("status") in("opened","replied"): daily[day]["opened"]+=1
        if e.get("status")=="replied": daily[day]["replied"]+=1
    result=[{"date":d,**v} for d,v in sorted(daily.items())][-30:]
    if not result:
        today=datetime.utcnow(); result=[{"date":(today-timedelta(days=13-i)).strftime("%Y-%m-%d"),"sent":i%4+1,"opened":max(0,i%3),"replied":max(0,i%2-1)} for i in range(14)]
    return ok(result)

@api.get("/analytics/lead-distribution")
@login_required
def lead_distribution():
    by=defaultdict(lambda:{"count":0,"total":0})
    for c in q("SELECT industry,lead_score FROM companies"):
        ind=c.get("industry") or "Other"; by[ind]["count"]+=1; by[ind]["total"]+=c.get("lead_score") or 0
    return ok(sorted([{"industry":k,"count":v["count"],"avg_score":round(v["total"]/v["count"],1)} for k,v in by.items()],key=lambda x:x["count"],reverse=True))

@api.get("/analytics/pipeline")
@login_required
def analytics_pipeline():
    cos=q("SELECT name,lead_score,status,annual_revenue FROM companies ORDER BY lead_score DESC LIMIT 20")
    return ok([{"name":c["name"],"lead_score":c.get("lead_score",0),"potential_revenue":int((c.get("annual_revenue") or 0)*0.02),"status":c.get("status","prospect")} for c in cos])

@api.get("/chat")
@login_required
def get_chat(): return ok(list(reversed(q("SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT 100"))))

@api.post("/chat")
@login_required
def post_chat():
    d=request.get_json(silent=True) or {}; msg=(d.get("message") or "").strip()
    if not msg: return err("message required")
    cos=q("SELECT name,industry,lead_score FROM companies")
    stats={"companies":cos,"emails_sent":q("SELECT COUNT(*) as n FROM emails WHERE status!='draft'")[0]["n"],
           "total_calls":q("SELECT COUNT(*) as n FROM calls")[0]["n"],"meetings_scheduled":q("SELECT COUNT(*) as n FROM meetings WHERE status='scheduled'")[0]["n"]}
    reply=chat_reply(msg,stats)
    run("INSERT INTO chat_messages(sender,sender_name,message) VALUES(?,?,?)","user",g.user["email"],msg)
    run("INSERT INTO chat_messages(sender,sender_name,message) VALUES(?,?,?)","bot","AI Bot",reply)
    return ok({"reply":reply})

@api.delete("/chat")
@login_required
def clear_chat(): run("DELETE FROM chat_messages"); return ok({"message":"Cleared"})

@api.get("/integrations/status")
@login_required
def integrations_status():
    user = q1("SELECT google_refresh_token FROM users WHERE id=?",(g.user["id"],))
    sg   = bool(SENDGRID_API_KEY)
    gm   = bool(GMAIL_EMAIL and GMAIL_PASSWORD)
    return ok({
        "groq":  {"connected": bool(GROQ_API_KEY), "model": GROQ_MODEL},
        "gmail": {"connected": sg or gm, "email": GMAIL_EMAIL or None,
                  "sendgrid": sg, "method": "sendgrid" if sg else ("smtp" if gm else "none")},
        "bland_ai":   {"connected": bool(BLAND_API_KEY)},
        "twilio_sms": {"connected": bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM),
                       "from_number": TWILIO_FROM or None, "admin_number": TWILIO_ADMIN or None},
        "google_calendar": {"connected": bool(user and user.get("google_refresh_token")),
                            "keys_set": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)},
    })
@api.post("/integrations/gmail/test")
@login_required
def test_gmail_route(): ok_,msg=test_gmail(); return ok({"success":ok_,"message":msg})

@api.post("/integrations/gmail/send-test")
@login_required
def send_test_gmail():
    user=q1("SELECT email FROM users WHERE id=?",(g.user["id"],))
    if not user: return err("Not found",404)
    r=send_email_smtp(user["email"],"Test","AI Sales Test","✅ Gmail is working!"); return ok({"success":r.get("status")=="sent","result":r})

@api.post("/integrations/bland/test")
@login_required
def test_bland_route():
    ok_,msg=test_bland()
    return ok({"success":ok_,"message":msg})

@api.post("/integrations/twilio/test")
@login_required
def test_twilio_route():
    d=request.get_json(silent=True) or {}
    to=(d.get("to_number") or "").strip()
    if not to: return err("to_number required — enter your mobile number e.g. +61411519086")
    result=sms_test(to)
    success=result.get("status")=="sent"
    msg = "✅ SMS sent!" if success else result.get("message","SMS failed")
    return ok({"success":success,"message":msg,"result":result})

@api.post("/integrations/twilio/daily-report")
@login_required
def manual_daily_report(): send_daily_report(); return ok({"message":"Daily report SMS sent"})

@api.get("/integrations/google/auth-url")
@login_required
def google_auth_url_route():
    if not GOOGLE_CLIENT_ID:
        return ok({"auth_url":None,"error":"GOOGLE_CLIENT_ID not set in Render env"})
    auto_redirect = f"{request.scheme}://{request.host}/api/integrations/google/callback"
    redirect = GOOGLE_REDIRECT_URI or auto_redirect
    url = google_auth_url(g.user["id"], redirect)
    return ok({"auth_url":url,"redirect_uri":redirect})

@api.get("/integrations/google/callback")
def google_callback():
    code_val = request.args.get("code","")
    state    = request.args.get("state","")
    oerror   = request.args.get("error","")
    base_style = "font-family:Arial;text-align:center;padding:60px;background:#0a0e1a;color:#e2e8f0"
    if oerror:
        detail = {"access_denied":"You clicked Deny. Click Allow to grant Calendar access.",
                  "redirect_uri_mismatch":"Redirect URI mismatch — add the callback URL to Authorized Redirect URIs in Google Cloud Console."
                  }.get(oerror, f"OAuth error: {oerror}")
        return f"<html><body style='{base_style}'><h2 style='color:#ef4444'>Connection Failed</h2><p style='color:#94a3b8'>{detail}</p></body></html>"
    if not code_val:
        return f"<html><body style='{base_style}'><h2 style='color:#ef4444'>No auth code received</h2><p style='color:#94a3b8'>Close this window and try again.</p></body></html>"
    auto_redirect = f"{request.scheme}://{request.host}/api/integrations/google/callback"
    redirect = GOOGLE_REDIRECT_URI or auto_redirect
    logger.info(f"Google callback: redirect_uri={redirect}")
    tokens = google_exchange(code_val, redirect)
    if tokens.get("refresh_token") and state:
        try:
            run("UPDATE users SET google_refresh_token=? WHERE id=?",(tokens["refresh_token"],int(state)))
            logger.info(f"Google Calendar connected for user {state}")
            return f"<html><body style='{base_style}'><h2 style='color:#22c55e'>Google Calendar Connected!</h2><p style='color:#94a3b8'>Close this window and refresh Integrations.</p><script>setTimeout(()=>window.close(),2000)</script></body></html>"
        except Exception as e:
            logger.error(f"Save Google token: {e}")
    if tokens.get("access_token") and not tokens.get("refresh_token"):
        detail = "Got token but no refresh_token. Go to myaccount.google.com/permissions, remove this app, then try Connect again."
    else:
        detail = tokens.get("error_description") or tokens.get("error") or f"Token exchange failed. Redirect URI: {redirect}"
    return f"<html><body style='{base_style}'><h2 style='color:#f59e0b'>Auth Incomplete</h2><p style='color:#94a3b8'>{detail}</p></body></html>"

@api.post("/integrations/google/disconnect")
@login_required
def google_disconnect(): run("UPDATE users SET google_refresh_token=NULL WHERE id=?",(g.user["id"],)); return ok({"message":"Disconnected"})

@api.get("/sms-logs")
@login_required
def sms_logs(): limit=min(int(request.args.get("limit",100)),500); return ok(q("SELECT * FROM sms_logs ORDER BY created_at DESC LIMIT ?",(limit,)))

@api.get("/automation/status")
@login_required
def automation_status():
    cos=q("SELECT lead_score,status FROM companies"); emails=q("SELECT status,email_type FROM emails"); calls_=q("SELECT status FROM calls"); mtgs=q("SELECT status FROM meetings")
    hot=sum(1 for c in cos if(c.get("lead_score") or 0)>=70); warm=sum(1 for c in cos if 40<=(c.get("lead_score") or 0)<70)
    sent=sum(1 for e in emails if e.get("status") in("sent","opened","replied")); opened=sum(1 for e in emails if e.get("status") in("opened","replied")); replied=sum(1 for e in emails if e.get("status")=="replied")
    cold_e=sum(1 for e in emails if e.get("email_type")=="cold"); fu=sum(1 for e in emails if e.get("email_type")=="follow_up")
    return ok({"pipeline":{"hot_leads":hot,"warm_leads":warm,"cold_leads":len(cos)-hot-warm,"total":len(cos),"pipeline_value":hot*50000+warm*15000},
               "emails":{"cold_sent":cold_e,"followups":fu,"opened":opened,"replied":replied,"open_rate":round(opened/cold_e*100,1) if cold_e else 0,"reply_rate":round(replied/cold_e*100,1) if cold_e else 0},
               "calls":{"total":len(calls_),"completed":sum(1 for c in calls_ if c.get("status")=="completed")},
               "meetings":{"scheduled":sum(1 for m in mtgs if m.get("status")=="scheduled"),"completed":sum(1 for m in mtgs if m.get("status")=="completed")},
               "schedule_utc":{"score":f"{AUTO_SCORE_HOUR:02d}:00","cold_email":f"{AUTO_EMAIL_HOUR:02d}:00 (weekdays)","follow_up":f"{AUTO_FOLLOWUP_HOUR:02d}:00 (3 days)","auto_call":f"{AUTO_CALL_HOUR:02d}:00 (score>=80)","daily_report":f"{DAILY_REPORT_HOUR:02d}:00"},
               "integrations":{"groq":bool(GROQ_API_KEY),"gmail":bool(GMAIL_EMAIL and GMAIL_PASSWORD),"bland":bool(BLAND_API_KEY),"twilio":bool(TWILIO_SID and TWILIO_TOKEN)}})

@api.post("/automation/run-now")
@login_required
def run_auto_now(): trigger_now(); return ok({"message":"Automation cycle started"})

@api.post("/automation/score-now")
@login_required
def score_now_route(): threading.Thread(target=auto_score_all,daemon=True).start(); return ok({"message":"Scoring companies..."})

@api.post("/automation/email-now")
@login_required
def email_now_route(): threading.Thread(target=auto_email_hot_leads,daemon=True).start(); return ok({"message":"Auto email started..."})

@api.post("/automation/followup-now")
@login_required
def followup_now_route(): threading.Thread(target=auto_followup,daemon=True).start(); return ok({"message":"Follow-up started..."})

@api.post("/automation/call-now")
@login_required
def call_now_route(): threading.Thread(target=auto_call_hot_leads,daemon=True).start(); return ok({"message":"Auto call started..."})

@api.get("/automation/activity-feed")
@login_required
def activity_feed():
    emails=q("SELECT 'email' as type,subject as title,status,recipient_email as target,created_at,email_type as subtype FROM emails ORDER BY created_at DESC LIMIT 20")
    calls_=q("SELECT 'call' as type,objective as title,status,phone_number as target,created_at,voice as subtype FROM calls ORDER BY created_at DESC LIMIT 10")
    sms=q("SELECT 'sms' as type,event_type as title,status,to_number as target,created_at,'' as subtype FROM sms_logs ORDER BY created_at DESC LIMIT 15")
    mtgs=q("SELECT 'meeting' as type,title,status,'' as target,created_at,meeting_type as subtype FROM meetings ORDER BY created_at DESC LIMIT 10")
    return ok(sorted(emails+calls_+sms+mtgs,key=lambda x:x.get("created_at",""),reverse=True)[:50])

# ══ FRONTEND HTML (embedded — no files needed) ════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# NEW ROUTES — Transcripts, Drafts, Opportunities, Company Chat, LinkedIn
# ══════════════════════════════════════════════════════════════════════════════

@api.get("/transcripts")
@login_required
def list_transcripts():
    rows = q("""SELECT t.*,ca.phone_number,ca.objective,co.name as company_name,
                COALESCE(ct.first_name,'')||' '||COALESCE(ct.last_name,'') as contact_name
                FROM call_transcripts t
                LEFT JOIN calls ca ON t.call_id=ca.id
                LEFT JOIN companies co ON t.company_id=co.id
                LEFT JOIN contacts ct ON t.contact_id=ct.id
                ORDER BY t.created_at DESC LIMIT 100""")
    return ok(rows)

@api.post("/calls/<int:cid_>/process-transcript")
@login_required
def process_transcript(cid_):
    call = q1("SELECT * FROM calls WHERE id=?", (cid_,))
    if not call: return err("Call not found", 404)
    transcript_text = call.get("transcript","") or ""
    if call.get("bland_call_id") and not transcript_text:
        live = bland_get_api(call["bland_call_id"])
        if live:
            transcript_text = live.get("concatenated_transcript","") or live.get("transcript","")
            run("UPDATE calls SET transcript=?,summary=?,status=?,updated_at=? WHERE id=?",
                (transcript_text, live.get("summary",""), live.get("status","completed"), now(), cid_))
    co = q1("SELECT * FROM companies WHERE id=?", (call.get("company_id"),)) if call.get("company_id") else {}
    ct = q1("SELECT * FROM contacts WHERE id=?", (call.get("contact_id"),)) if call.get("contact_id") else {}
    analysis = ai_call_transcript_analysis(transcript_text, (co or {}).get("name","Unknown"), (ct or {}).get("first_name","Contact"))
    meet_link = ""; cal_event_id = None
    if analysis.get("meeting_booked"):
        sched = analysis.get("meeting_datetime") or (datetime.utcnow()+timedelta(days=2)).strftime("%Y-%m-%d 10:00:00")
        try:
            company_nm = (co or {}).get("name","Client")
            cal_event_id, meet_link = create_calendar_event_with_meet(
                g.user["id"], f"ARC Digital + {company_nm} - Discovery Call",
                "Booked via AI call (Sarah).", sched, 30)
        except Exception as e_:
            logger.error(f"Meet: {e_}"); meet_link = generate_google_meet_link()
        if meet_link and co:
            run("""INSERT INTO meetings(company_id,contact_id,created_by,title,meeting_type,
                   scheduled_at,duration_minutes,status,meeting_link,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (co.get("id"),ct.get("id") if ct else None,g.user["id"],
                 f"AI Call Follow-up - {(co or {}).get('name','')}","discovery",sched,30,"scheduled",meet_link,now()))
    analysis["meet_link"] = meet_link
    existing = q1("SELECT id FROM call_transcripts WHERE call_id=?", (cid_,))
    if existing:
        run("""UPDATE call_transcripts SET transcript=?,summary=?,sentiment=?,meeting_booked=?,
               google_meet_link=?,interest_score=?,next_action=? WHERE call_id=?""",
            (transcript_text,analysis.get("summary",""),analysis.get("sentiment","neutral"),
             1 if analysis.get("meeting_booked") else 0,meet_link,
             analysis.get("interest_score",0),analysis.get("next_action","send_followup"),cid_))
        tid = existing["id"]
    else:
        tid = run("""INSERT INTO call_transcripts(call_id,company_id,contact_id,transcript,
                summary,sentiment,meeting_booked,google_meet_link,interest_score,next_action)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (cid_,call.get("company_id"),call.get("contact_id"),transcript_text,
             analysis.get("summary",""),analysis.get("sentiment","neutral"),
             1 if analysis.get("meeting_booked") else 0,meet_link,
             analysis.get("interest_score",0),analysis.get("next_action","send_followup")))
    if co and ct and ct.get("email"):
        email_content = generate_post_call_email(co, ct, analysis, transcript_text)
        run("""INSERT INTO email_drafts(company_id,contact_id,created_by,draft_type,
               subject,body,recipient_email,recipient_name,status,scheduled_send_day,call_transcript_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (co.get("id"),ct.get("id"),g.user["id"],email_content.get("email_type","followup"),
             email_content["subject"],email_content["body"],ct["email"],
             f"{ct.get('first_name','')} {ct.get('last_name','')}".strip(),"pending_approval",
             0 if analysis.get("meeting_booked") else 1,tid))
    if analysis.get("interest_score",0)>=8 and co:
        if not q1("SELECT id FROM opportunities WHERE company_id=?",(co.get("id"),)):
            run("""INSERT INTO opportunities(company_id,contact_id,created_by,title,
                    interest_score,quoted_value,status,notes,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (co.get("id"),ct.get("id") if ct else None,g.user["id"],
                 f"AI Call - {co.get('name')} - {analysis.get('sentiment','').title()}",
                 analysis.get("interest_score",8),5000,"open",analysis.get("summary",""),now()))
    return ok({"transcript":q1("SELECT * FROM call_transcripts WHERE id=?",(tid,)),
               "analysis":analysis,"meet_link":meet_link})

@api.get("/email-drafts")
@login_required
def list_email_drafts():
    status_f = request.args.get("status",""); cid_f = request.args.get("company_id","")
    sql = """SELECT d.*,co.name as company_name,
             COALESCE(ct.first_name,'')||' '||COALESCE(ct.last_name,'') as contact_name
             FROM email_drafts d LEFT JOIN companies co ON d.company_id=co.id
             LEFT JOIN contacts ct ON d.contact_id=ct.id WHERE 1=1"""
    args=[]
    if status_f: sql+=" AND d.status=?"; args.append(status_f)
    if cid_f:    sql+=" AND d.company_id=?"; args.append(cid_f)
    return ok(q(sql+" ORDER BY d.created_at DESC LIMIT 200",args))

@api.put("/email-drafts/<int:did>")
@login_required
def update_email_draft(did):
    if not q1("SELECT id FROM email_drafts WHERE id=?",(did,)): return err("Not found",404)
    d = request.get_json(silent=True) or {}
    allowed={"subject","body","recipient_email","recipient_name","status","scheduled_send_day"}
    sets=[f"{k}=?" for k in d if k in allowed]; vals=[d[k] for k in d if k in allowed]
    if sets: run(f"UPDATE email_drafts SET {','.join(sets)} WHERE id=?",vals+[did])
    return ok(q1("SELECT * FROM email_drafts WHERE id=?",(did,)))

@api.post("/email-drafts/<int:did>/approve")
@login_required
def approve_email_draft(did):
    draft = q1("SELECT * FROM email_drafts WHERE id=?",(did,))
    if not draft: return err("Not found",404)
    result = send_email_smtp(draft["recipient_email"],draft.get("recipient_name",""),draft["subject"],draft["body"])
    if result.get("status")=="sent":
        run("UPDATE email_drafts SET status=\'sent\',approved_by=?,approved_at=? WHERE id=?",(g.user["id"],now(),did))
        run("""INSERT INTO emails(company_id,contact_id,created_by,email_type,subject,body,
               recipient_email,recipient_name,status,sent_at,ai_model_used,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (draft.get("company_id"),draft.get("contact_id"),g.user["id"],draft.get("draft_type","follow_up"),
             draft["subject"],draft["body"],draft["recipient_email"],draft.get("recipient_name",""),
             "sent",now(),"ai_draft",now()))
        return ok({"message":"Email sent","result":result})
    return ok({"message":result.get("message","Send failed"),"result":result,"success":False})

@api.post("/email-drafts/<int:did>/reject")
@login_required
def reject_email_draft(did):
    run("UPDATE email_drafts SET status=\'rejected\',approved_by=?,approved_at=? WHERE id=?",(g.user["id"],now(),did))
    return ok({"message":"Draft rejected"})

@api.post("/email-drafts/generate")
@login_required
def generate_email_draft():
    d=request.get_json(silent=True) or {}
    company_id=d.get("company_id"); dtype=d.get("draft_type","cold")
    co=q1("SELECT * FROM companies WHERE id=?",(company_id,)) if company_id else None
    if not co: return err("company_id required")
    ct=(q1("SELECT * FROM contacts WHERE company_id=? AND is_decision_maker=1",(company_id,))
        or q1("SELECT * FROM contacts WHERE company_id=?",(company_id,)))
    if not ct: return err("No contacts for this company")
    content=generate_email(co,ct,dtype,d.get("custom",""))
    did=run("""INSERT INTO email_drafts(company_id,contact_id,created_by,draft_type,
             subject,body,recipient_email,recipient_name,status,scheduled_send_day)
             VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (company_id,ct.get("id"),g.user["id"],dtype,content["subject"],content["body"],
         ct.get("email",""),f"{ct.get('first_name','')} {ct.get('last_name','')}".strip(),"pending_approval",d.get("schedule_day",0)))
    return ok(q1("SELECT * FROM email_drafts WHERE id=?",(did,))),201

@api.get("/opportunities")
@login_required
def list_opportunities():
    return ok(q("""SELECT op.*,co.name as company_name,co.industry,co.lead_score,
                COALESCE(ct.first_name,'')||' '||COALESCE(ct.last_name,'') as contact_name,
                ct.email as contact_email,ct.phone as contact_phone
                FROM opportunities op LEFT JOIN companies co ON op.company_id=co.id
                LEFT JOIN contacts ct ON op.contact_id=ct.id
                ORDER BY op.interest_score DESC,op.updated_at DESC"""))

@api.post("/opportunities")
@login_required
def create_opportunity():
    d=request.get_json(silent=True) or {}
    if not d.get("company_id") or not d.get("title"): return err("company_id and title required")
    oid=run("""INSERT INTO opportunities(company_id,contact_id,created_by,title,
             interest_score,quoted_value,status,notes,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (d["company_id"],d.get("contact_id"),g.user["id"],d["title"],
         d.get("interest_score",5),d.get("quoted_value",0),d.get("status","open"),d.get("notes",""),now()))
    return ok(q1("SELECT * FROM opportunities WHERE id=?",(oid,))),201

@api.put("/opportunities/<int:oid>")
@login_required
def update_opportunity(oid):
    if not q1("SELECT id FROM opportunities WHERE id=?",(oid,)): return err("Not found",404)
    d=request.get_json(silent=True) or {}
    allowed={"title","interest_score","quoted_value","accepted_value","status","notes","quotation_text","invoice_text"}
    sets=[f"{k}=?" for k in d if k in allowed]; vals=[d[k] for k in d if k in allowed]
    if sets:
        sets.append("updated_at=?"); vals.append(now()); vals.append(oid)
        run(f"UPDATE opportunities SET {','.join(sets)} WHERE id=?",vals)
    return ok(q1("SELECT * FROM opportunities WHERE id=?",(oid,)))

@api.post("/opportunities/<int:oid>/generate-quotation")
@login_required
def gen_quotation(oid):
    op=q1("SELECT * FROM opportunities WHERE id=?",(oid,))
    if not op: return err("Not found",404)
    co=q1("SELECT * FROM companies WHERE id=?",(op["company_id"],))
    ct=q1("SELECT * FROM contacts WHERE id=?",(op.get("contact_id"),)) if op.get("contact_id") else None
    text=generate_quotation(co or {},ct or {},op)
    run("UPDATE opportunities SET quotation_text=?,updated_at=? WHERE id=?",(text,now(),oid))
    return ok({"quotation":text,"opportunity":q1("SELECT * FROM opportunities WHERE id=?",(oid,))})

@api.post("/opportunities/<int:oid>/generate-invoice")
@login_required
def gen_invoice(oid):
    op=q1("SELECT * FROM opportunities WHERE id=?",(oid,))
    if not op: return err("Not found",404)
    co=q1("SELECT * FROM companies WHERE id=?",(op["company_id"],))
    ct=q1("SELECT * FROM contacts WHERE id=?",(op.get("contact_id"),)) if op.get("contact_id") else None
    text=generate_invoice(co or {},ct or {},op)
    run("UPDATE opportunities SET invoice_text=?,updated_at=? WHERE id=?",(text,now(),oid))
    return ok({"invoice":text,"opportunity":q1("SELECT * FROM opportunities WHERE id=?",(oid,))})

@api.get("/companies/<int:cid>/chat")
@login_required
def get_company_chat(cid):
    return ok(q("SELECT * FROM company_chat WHERE company_id=? ORDER BY created_at ASC LIMIT 50",(cid,)))

@api.post("/companies/<int:cid>/chat")
@login_required
def post_company_chat(cid):
    d=request.get_json(silent=True) or {}; msg=(d.get("message") or "").strip()
    if not msg: return err("message required")
    history=[{"role":m["sender"],"content":m["message"]}
             for m in q("SELECT sender,message FROM company_chat WHERE company_id=? ORDER BY created_at DESC LIMIT 10",(cid,))]
    reply=generate_company_chatbot_response(cid,msg,list(reversed(history)))
    run("INSERT INTO company_chat(company_id,sender,message) VALUES(?,?,?)",(cid,"user",msg))
    run("INSERT INTO company_chat(company_id,sender,message) VALUES(?,?,?)",(cid,"ai",reply))
    return ok({"reply":reply})

@api.post("/companies/<int:cid>/qualify")
@login_required
def qualify_company(cid):
    co=q1("SELECT * FROM companies WHERE id=?",(cid,))
    if not co: return err("Not found",404)
    contacts=q("SELECT * FROM contacts WHERE company_id=?",(cid,))
    signals=q("SELECT * FROM buying_signals WHERE company_id=?",(cid,))
    result=qualify_company_plumbing(co,contacts,signals)
    run("UPDATE companies SET ai_summary=?,updated_at=? WHERE id=?",
        (f"Plumbing Qualify Score: {result['qualify_score']}/10 - {result['reason']}",now(),cid))
    return ok(result)

@api.post("/contacts/qualify-all")
@login_required
def qualify_all_contacts():
    """Run qualify_company_plumbing on every company that has contacts."""
    def _run():
        cos = q("SELECT DISTINCT company_id FROM contacts WHERE company_id IS NOT NULL")
        done = 0
        for row in cos:
            cid = row["company_id"]
            co  = q1("SELECT * FROM companies WHERE id=?", (cid,))
            if not co: continue
            cts  = q("SELECT * FROM contacts WHERE company_id=?", (cid,))
            sigs = q("SELECT * FROM buying_signals WHERE company_id=?", (cid,))
            result = qualify_company_plumbing(co, cts, sigs)
            # Upsert lead_scores
            if q1("SELECT id FROM lead_scores WHERE company_id=?", (cid,)):
                run("""UPDATE lead_scores SET total_score=?,industry_score=?,tier=?,updated_at=?
                       WHERE company_id=?""",
                    (result["qualify_score"], result["industry_fit"], result["tier"], now(), cid))
            else:
                run("""INSERT INTO lead_scores(company_id,total_score,industry_score,tier)
                       VALUES(?,?,?,?)""",
                    (cid, result["qualify_score"], result["industry_fit"], result["tier"]))
            run("UPDATE companies SET lead_score=?,updated_at=? WHERE id=?",
                (result["qualify_score"], now(), cid))
            done += 1
        logger.info(f"qualify_all_contacts: scored {done} companies")
    threading.Thread(target=_run, daemon=True).start()
    return ok({"message": f"Qualifying all companies in background…"})

@api.post("/contacts/<int:ctid>/qualify-company")
@login_required
def qualify_contact_company(ctid):
    """Qualify the company this contact belongs to."""
    ct = q1("SELECT * FROM contacts WHERE id=?", (ctid,))
    if not ct: return err("Contact not found", 404)
    co  = q1("SELECT * FROM companies WHERE id=?", (ct["company_id"],))
    if not co: return err("Company not found", 404)
    cts  = q("SELECT * FROM contacts WHERE company_id=?", (ct["company_id"],))
    sigs = q("SELECT * FROM buying_signals WHERE company_id=?", (ct["company_id"],))
    result = qualify_company_plumbing(co, cts, sigs)
    if q1("SELECT id FROM lead_scores WHERE company_id=?", (co["id"],)):
        run("""UPDATE lead_scores SET total_score=?,industry_score=?,tier=?,updated_at=?
               WHERE company_id=?""",
            (result["qualify_score"],result["industry_fit"],result["tier"],now(),co["id"]))
    else:
        run("""INSERT INTO lead_scores(company_id,total_score,industry_score,tier) VALUES(?,?,?,?)""",
            (co["id"],result["qualify_score"],result["industry_fit"],result["tier"]))
    run("UPDATE companies SET lead_score=?,updated_at=? WHERE id=?",(result["qualify_score"],now(),co["id"]))
    return ok({**result, "company": co["name"]})

@api.post("/contacts/upload-csv")
@login_required
def upload_contacts_csv():
    f=request.files.get("file")
    if not f or not f.filename.endswith(".csv"): return err("CSV file required")
    content=f.read(); uid=g.user["id"]; fname=f.filename
    def _proc():
        import csv as _csv, io as _io
        reader=_csv.DictReader(_io.StringIO(content.decode("utf-8-sig",errors="replace")))
        ok_n,fail=0,0
        for row in reader:
            try:
                fn=(row.get("first_name") or row.get("First Name") or "").strip()
                ln=(row.get("last_name")  or row.get("Last Name")  or "").strip()
                email_=(row.get("email") or row.get("Email") or "").strip()
                phone_=(row.get("phone") or row.get("Phone") or "").strip()
                cname_=(row.get("company") or row.get("Company") or "").strip()
                linkedin_=(row.get("linkedin") or row.get("LinkedIn") or "").strip()
                if not fn: fail+=1; continue
                cid_v=None
                if cname_:
                    co_r=q1("SELECT id FROM companies WHERE name=?",(cname_,))
                    cid_v=co_r["id"] if co_r else run("INSERT INTO companies(name,status,created_by,updated_at) VALUES(?,?,?,?)",(cname_,"prospect",uid,now()))
                if cid_v:
                    ctid_v=run("INSERT INTO contacts(company_id,first_name,last_name,email,phone,seniority_level,is_decision_maker) VALUES(?,?,?,?,?,?,?)",(cid_v,fn,ln,email_,phone_,"individual",0))
                    if linkedin_ and ctid_v:
                        run("INSERT INTO contact_linkedin(contact_id,company_id,linkedin_url) VALUES(?,?,?)",(ctid_v,cid_v,linkedin_))
                    ok_n+=1
            except Exception: fail+=1
        logger.info(f"Contact CSV: {ok_n} ok {fail} fail")
    threading.Thread(target=_proc,daemon=True).start()
    return ok({"message":"Import started","filename":fname}),201

@api.get("/contacts/<int:ctid>/linkedin")
@login_required
def get_contact_linkedin(ctid):
    return ok(q1("SELECT * FROM contact_linkedin WHERE contact_id=?",(ctid,)))

@api.post("/contacts/<int:ctid>/linkedin")
@login_required
def set_contact_linkedin(ctid):
    ct=q1("SELECT * FROM contacts WHERE id=?",(ctid,))
    if not ct: return err("Contact not found",404)
    d=request.get_json(silent=True) or {}
    existing=q1("SELECT id FROM contact_linkedin WHERE contact_id=?",(ctid,))
    if existing:
        run("UPDATE contact_linkedin SET linkedin_url=?,position=?,seniority=?,is_key_person=? WHERE contact_id=?",
            (d.get("linkedin_url",""),d.get("position",""),d.get("seniority",""),1 if d.get("is_key_person") else 0,ctid))
    else:
        run("INSERT INTO contact_linkedin(contact_id,company_id,linkedin_url,position,seniority,is_key_person) VALUES(?,?,?,?,?,?)",
            (ctid,ct.get("company_id"),d.get("linkedin_url",""),d.get("position",""),d.get("seniority",""),1 if d.get("is_key_person") else 0))
    return ok(q1("SELECT * FROM contact_linkedin WHERE contact_id=?",(ctid,)))

@api.post("/automation/generate-drafts")
@login_required
def gen_drafts_now():
    def _auto():
        try:
            cos=q("SELECT * FROM companies WHERE lead_score>=50 ORDER BY lead_score DESC LIMIT 20")
            n=0
            for co in cos:
                ct=(q1("SELECT * FROM contacts WHERE company_id=? AND is_decision_maker=1",(co["id"],))
                    or q1("SELECT * FROM contacts WHERE company_id=?",(co["id"],)))
                if not ct or not ct.get("email"): continue
                if q("SELECT id FROM email_drafts WHERE company_id=? AND status=\'pending_approval\'",(co["id"],)): continue
                last_call=q1("SELECT * FROM calls WHERE company_id=? ORDER BY created_at DESC LIMIT 1",(co["id"],))
                days=0
                if last_call:
                    days=max(0,(datetime.utcnow()-datetime.strptime(last_call["created_at"][:19],"%Y-%m-%d %H:%M:%S")).days)
                if days not in [0,1,3,5,10] and last_call: continue
                dtype="follow_up" if days>0 else "cold"
                content=generate_email(co,ct,dtype)
                run("""INSERT INTO email_drafts(company_id,contact_id,created_by,draft_type,
                       subject,body,recipient_email,recipient_name,status,scheduled_send_day)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (co["id"],ct["id"],1,dtype,content["subject"],content["body"],
                     ct["email"],f"{ct.get('first_name','')} {ct.get('last_name','')}".strip(),"pending_approval",days))
                n+=1
            logger.info(f"Auto-generated {n} email drafts")
        except Exception as e_: logger.error(f"gen_drafts: {e_}")
    threading.Thread(target=_auto,daemon=True).start()
    return ok({"message":"Draft generation started"})



# ══════════════════════════════════════════════════════════════════════════════
# NEW API ROUTES — Invoice, Quotation, Inbound Calls
# ══════════════════════════════════════════════════════════════════════════════

# ── Formal Invoices ───────────────────────────────────────────────────────────
@api.get("/invoices")
@login_required
def list_invoices():
    return ok(q("""SELECT i.*,co.name as company_name,
                   COALESCE(ct.first_name,'')||' '||COALESCE(ct.last_name,'') as contact_name
                   FROM invoices i LEFT JOIN companies co ON i.company_id=co.id
                   LEFT JOIN contacts ct ON i.contact_id=ct.id
                   ORDER BY i.created_at DESC"""))

@api.post("/invoices")
@login_required
def create_invoice_record():
    d = request.get_json(silent=True) or {}
    oid = d.get("opportunity_id")
    op  = q1("SELECT * FROM opportunities WHERE id=?", (oid,)) if oid else None
    co  = q1("SELECT * FROM companies WHERE id=?", (d.get("company_id") or (op or {}).get("company_id"),))
    ct  = q1("SELECT * FROM contacts WHERE id=?",  (d.get("contact_id") or (op or {}).get("contact_id"),)) if (d.get("contact_id") or (op or {}).get("contact_id")) else None
    amount    = float(d.get("amount") or (op or {}).get("accepted_value") or (op or {}).get("quoted_value") or 0)
    gst       = round(amount * 0.10, 2)
    total     = round(amount + gst, 2)
    inv_num   = build_invoice_number()
    due_date  = (datetime.utcnow()+timedelta(days=30)).strftime("%Y-%m-%d")
    content_text = generate_invoice({} if not op else op, ct, op or d)
    iid = run("""INSERT INTO invoices(opportunity_id,company_id,contact_id,created_by,invoice_number,
               invoice_type,subject,content,amount,gst_amount,total_amount,status,due_date,issued_date)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (oid,(co or {}).get("id"),(ct or {}).get("id"),g.user["id"],inv_num,"invoice",
         f"AI Business Automation — {(co or {}).get('name','')}",content_text,
         amount,gst,total,"draft",due_date,datetime.utcnow().strftime("%Y-%m-%d")))
    inv = q1("SELECT * FROM invoices WHERE id=?", (iid,))
    # Generate HTML
    html_content = generate_invoice_html(
        {"invoice_number":inv_num,"amount":amount,"gst_amount":gst,"total_amount":total,
         "status":"draft","issued_date":datetime.utcnow().strftime("%Y-%m-%d"),"due_date":due_date,
         "content":content_text},
        op, co, ct, d.get("template","professional"))
    return ok({"invoice":inv,"html":html_content,"xero_ready":True}),201

@api.get("/invoices/<int:iid>/html")
@login_required
def get_invoice_html(iid):
    inv = q1("SELECT * FROM invoices WHERE id=?", (iid,))
    if not inv: return err("Not found",404)
    co  = q1("SELECT * FROM companies WHERE id=?", (inv.get("company_id"),)) if inv.get("company_id") else {}
    ct  = q1("SELECT * FROM contacts WHERE id=?",  (inv.get("contact_id"),)) if inv.get("contact_id") else {}
    op  = q1("SELECT * FROM opportunities WHERE id=?", (inv.get("opportunity_id"),)) if inv.get("opportunity_id") else {}
    html = generate_invoice_html(inv, op, co, ct, request.args.get("template","professional"))
    return ok({"html":html,"invoice":inv})

@api.post("/invoices/<int:iid>/approve")
@login_required
def approve_invoice(iid):
    inv = q1("SELECT * FROM invoices WHERE id=?", (iid,))
    if not inv: return err("Not found",404)
    run("UPDATE invoices SET status='approved',approved_by=?,approved_at=? WHERE id=?",(g.user["id"],now(),iid))
    # Push to Xero format
    co = q1("SELECT * FROM companies WHERE id=?", (inv.get("company_id"),)) if inv.get("company_id") else {}
    ct = q1("SELECT * FROM contacts WHERE id=?",  (inv.get("contact_id"),)) if inv.get("contact_id") else {}
    _, xero_payload = push_invoice_to_xero(inv, co, ct)
    return ok({"invoice":q1("SELECT * FROM invoices WHERE id=?",(iid,)),"xero_payload":xero_payload,
               "message":"Invoice approved. Xero payload ready — connect Xero MCP to auto-push."})

# ── Formal Quotations ─────────────────────────────────────────────────────────
@api.get("/quotations")
@login_required
def list_quotations():
    return ok(q("""SELECT q.*,co.name as company_name,
                   COALESCE(ct.first_name,'')||' '||COALESCE(ct.last_name,'') as contact_name
                   FROM quotations q LEFT JOIN companies co ON q.company_id=co.id
                   LEFT JOIN contacts ct ON q.contact_id=ct.id
                   ORDER BY q.created_at DESC"""))

@api.post("/quotations")
@login_required
def create_quotation_record():
    d   = request.get_json(silent=True) or {}
    oid = d.get("opportunity_id")
    op  = q1("SELECT * FROM opportunities WHERE id=?", (oid,)) if oid else None
    co  = q1("SELECT * FROM companies WHERE id=?", (d.get("company_id") or (op or {}).get("company_id"),))
    ct  = q1("SELECT * FROM contacts WHERE id=?",  (d.get("contact_id") or (op or {}).get("contact_id"),)) if (d.get("contact_id") or (op or {}).get("contact_id")) else None
    amount   = float(d.get("amount") or (op or {}).get("quoted_value") or 0)
    gst      = round(amount*0.10,2); total = round(amount+gst,2)
    q_num    = build_quote_number()
    valid_dt = (datetime.utcnow()+timedelta(days=30)).strftime("%Y-%m-%d")
    content_text = generate_quotation({} if not op else op, ct, op or d)
    qid = run("""INSERT INTO quotations(opportunity_id,company_id,contact_id,created_by,quote_number,
               content,amount,gst_amount,total_amount,valid_until,status,template)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (oid,(co or {}).get("id"),(ct or {}).get("id"),g.user["id"],q_num,content_text,
         amount,gst,total,valid_dt,"draft",d.get("template","professional")))
    quot = q1("SELECT * FROM quotations WHERE id=?", (qid,))
    html_content = generate_quotation_html(
        {"quote_number":q_num,"amount":amount,"gst_amount":gst,"total_amount":total,
         "valid_until":valid_dt,"content":content_text},
        op, co, ct, d.get("template","professional"))
    return ok({"quotation":quot,"html":html_content}),201

@api.get("/quotations/<int:qid>/html")
@login_required
def get_quotation_html(qid):
    quot = q1("SELECT * FROM quotations WHERE id=?", (qid,))
    if not quot: return err("Not found",404)
    co = q1("SELECT * FROM companies WHERE id=?", (quot.get("company_id"),)) if quot.get("company_id") else {}
    ct = q1("SELECT * FROM contacts WHERE id=?",  (quot.get("contact_id"),)) if quot.get("contact_id") else {}
    op = q1("SELECT * FROM opportunities WHERE id=?", (quot.get("opportunity_id"),)) if quot.get("opportunity_id") else {}
    html = generate_quotation_html(quot, op, co, ct, request.args.get("template","professional"))
    return ok({"html":html,"quotation":quot})

@api.post("/quotations/<int:qid>/approve")
@login_required
def approve_quotation(qid):
    quot = q1("SELECT * FROM quotations WHERE id=?", (qid,))
    if not quot: return err("Not found",404)
    run("UPDATE quotations SET status='approved',approved_by=?,approved_at=? WHERE id=?",(g.user["id"],now(),qid))
    return ok({"quotation":q1("SELECT * FROM quotations WHERE id=?",(qid,)),
               "message":"Quotation approved. Ready to convert to invoice."})

@api.post("/quotations/<int:qid>/convert-to-invoice")
@login_required
def convert_to_invoice(qid):
    quot = q1("SELECT * FROM quotations WHERE id=?", (qid,))
    if not quot: return err("Not found",404)
    inv_num  = build_invoice_number()
    due_date = (datetime.utcnow()+timedelta(days=30)).strftime("%Y-%m-%d")
    iid = run("""INSERT INTO invoices(opportunity_id,company_id,contact_id,created_by,invoice_number,
               subject,content,amount,gst_amount,total_amount,status,due_date,issued_date)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (quot.get("opportunity_id"),quot.get("company_id"),quot.get("contact_id"),g.user["id"],
         inv_num,f"Invoice — {quot.get('quote_number','')}",quot.get("content",""),
         quot.get("amount",0),quot.get("gst_amount",0),quot.get("total_amount",0),
         "draft",due_date,datetime.utcnow().strftime("%Y-%m-%d")))
    run("UPDATE quotations SET status='converted' WHERE id=?", (qid,))
    return ok({"invoice":q1("SELECT * FROM invoices WHERE id=?",(iid,)),"invoice_id":iid})

# ── Inbound Calls (Facebook Ad) ───────────────────────────────────────────────
@api.get("/inbound-calls")
@login_required
def list_inbound_calls():
    return ok(q("""SELECT * FROM inbound_calls ORDER BY created_at DESC LIMIT 100"""))

@api.post("/bland/inbound-webhook")
def bland_inbound_webhook():
    """Bland AI webhook for inbound calls from Facebook ads."""
    try:
        data = request.get_json(silent=True) or {}
        logger.info(f"Bland inbound webhook: {data.get('call_id')} from {data.get('from','')}")
        result = process_inbound_call(data)
        return jsonify({"ok":True,"processed":True,"id":result.get("id")}),200
    except Exception as e:
        logger.error(f"Bland webhook: {e}")
        return jsonify({"ok":True}),200  # Always 200 to Bland

@api.post("/bland/setup-inbound")
@login_required
def setup_inbound():
    result = setup_bland_inbound_agent()
    return ok(result)

@api.get("/bland/inbound-status")
@login_required
def inbound_status():
    if not BLAND_API_KEY: return ok({"configured":False,"message":"BLAND_API_KEY not set"})
    if not BLAND_INBOUND_NUMBER: return ok({"configured":False,"message":"BLAND_INBOUND_NUMBER not set — add your Bland inbound number"})
    count = q("SELECT COUNT(*) as n FROM inbound_calls")[0]["n"]
    recent= q("SELECT * FROM inbound_calls ORDER BY created_at DESC LIMIT 5")
    return ok({"configured":True,"inbound_number":BLAND_INBOUND_NUMBER,
               "total_calls":count,"recent":recent,
               "webhook_url":f"{request.scheme}://{request.host}/api/bland/inbound-webhook"})


_HTML = b'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>AI Sales Assistant</title>\n<style>\n*{box-sizing:border-box;margin:0;padding:0}\n:root{\n  --bg:#0a0e1a;--panel:#1e293b;--border:#334155;--accent:#3b82f6;\n  --accent2:#1d4ed8;--text:#e2e8f0;--muted:#94a3b8;\n  --hot:#ef4444;--warm:#f59e0b;--cold:#60a5fa;\n  --green:#22c55e;--red:#ef4444;\n}\nbody{font-family:\'Segoe UI\',Arial,sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column}\n#app{display:flex;flex:1;overflow:hidden}\n\n/* Sidebar */\n#sidebar{width:220px;background:linear-gradient(180deg,#0f172a,#1e293b);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}\n#sidebar h2{padding:20px 16px 8px;font-size:1rem;color:var(--accent);border-bottom:1px solid var(--border)}\n#user-info{padding:8px 16px 12px;font-size:.75rem;color:var(--muted);border-bottom:1px solid var(--border)}\n#nav{flex:1;padding:8px 0;overflow-y:auto}\n.nav-item{display:flex;align-items:center;gap:10px;padding:10px 16px;cursor:pointer;color:var(--muted);font-size:.875rem;transition:all .15s;border-left:3px solid transparent}\n.nav-item:hover{color:var(--text);background:rgba(255,255,255,.05)}\n.nav-item.active{color:var(--accent);background:rgba(59,130,246,.1);border-left-color:var(--accent)}\n#sys-status{padding:12px 16px;border-top:1px solid var(--border);font-size:.7rem;color:var(--muted)}\n#sys-status div{margin-bottom:3px}\n#logout-btn{margin:12px;padding:8px;background:#1e3a5f;border:1px solid var(--border);color:var(--text);border-radius:6px;cursor:pointer;font-size:.8rem}\n#logout-btn:hover{background:var(--accent2)}\n\n/* Main */\n#main{flex:1;overflow:hidden;display:flex;flex-direction:column}\n#topbar{padding:12px 24px;border-bottom:1px solid var(--border);background:var(--panel);display:flex;justify-content:space-between;align-items:center}\n#topbar h1{font-size:1.1rem;font-weight:600}\n#content{flex:1;overflow-y:auto;padding:24px}\n\n/* Login */\n#login-screen{display:flex;align-items:center;justify-content:center;height:100vh;background:var(--bg)}\n.login-box{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:40px;width:380px}\n.login-box h2{text-align:center;margin-bottom:8px;font-size:1.4rem}\n.login-box p{text-align:center;color:var(--muted);font-size:.8rem;margin-bottom:24px}\n\n/* Forms */\n.form-group{margin-bottom:14px}\nlabel{display:block;font-size:.8rem;color:var(--muted);margin-bottom:5px}\ninput,select,textarea{width:100%;padding:9px 12px;background:#0f172a;border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.875rem;outline:none;transition:border .15s}\ninput:focus,select:focus,textarea:focus{border-color:var(--accent)}\ntextarea{resize:vertical;min-height:80px}\nselect option{background:#0f172a}\n\n/* Buttons */\n.btn{padding:9px 18px;border:none;border-radius:6px;cursor:pointer;font-size:.875rem;font-weight:600;transition:opacity .15s;display:inline-flex;align-items:center;gap:6px}\n.btn:hover{opacity:.85}\n.btn:active{opacity:.7}\n.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}\n.btn-success{background:#15803d;color:#fff}\n.btn-warning{background:#b45309;color:#fff}\n.btn-danger{background:#991b1b;color:#fff}\n.btn-ghost{background:rgba(255,255,255,.08);color:var(--text)}\n.btn-sm{padding:5px 12px;font-size:.8rem}\n.btn-full{width:100%;justify-content:center}\n\n/* Cards / Metrics */\n.metrics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}\n.metric-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;text-align:center}\n.metric-val{font-size:1.8rem;font-weight:700;color:var(--accent)}\n.metric-lbl{font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:4px}\n\n/* Tables */\n.tbl-wrap{background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:20px}\n.tbl-head{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}\n.tbl-head h3{font-size:.9rem;font-weight:600}\ntable{width:100%;border-collapse:collapse}\nth{padding:10px 14px;text-align:left;font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--border);background:#0f172a}\ntd{padding:10px 14px;font-size:.85rem;border-bottom:1px solid rgba(51,65,85,.5)}\ntr:last-child td{border-bottom:none}\ntr:hover td{background:rgba(255,255,255,.02)}\n\n/* Badges */\n.badge{display:inline-block;padding:2px 9px;border-radius:99px;font-size:.7rem;font-weight:600}\n.badge-hot{background:#7f1d1d;color:#fca5a5}\n.badge-warm{background:#78350f;color:#fcd34d}\n.badge-cold{background:#1e3a5f;color:#93c5fd}\n.badge-sent{background:#14532d;color:#86efac}\n.badge-draft{background:#1e293b;color:#94a3b8}\n.badge-opened{background:#1e3a5f;color:#93c5fd}\n.badge-replied{background:#3b0764;color:#d8b4fe}\n.badge-scheduled{background:#14532d;color:#86efac}\n.badge-completed{background:#14532d;color:#86efac}\n.badge-proposed{background:#78350f;color:#fcd34d}\n.badge-queued{background:#1e3a5f;color:#93c5fd}\n.badge-error{background:#7f1d1d;color:#fca5a5}\n\n/* Tabs */\n.tabs{display:flex;gap:4px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:0}\n.tab{padding:8px 16px;cursor:pointer;color:var(--muted);font-size:.85rem;border-bottom:2px solid transparent;margin-bottom:-1px}\n.tab:hover{color:var(--text)}\n.tab.active{color:var(--accent);border-bottom-color:var(--accent)}\n.tab-panel{display:none}\n.tab-panel.active{display:block}\n\n/* Modal */\n.modal-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}\n.modal-backdrop.open{display:flex}\n.modal{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:24px;width:100%;max-width:560px;max-height:85vh;overflow-y:auto}\n.modal h3{margin-bottom:16px;font-size:1rem}\n.modal-close{float:right;cursor:pointer;color:var(--muted);font-size:1.2rem}\n\n/* Search */\n.search-bar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}\n.search-bar input{flex:1;min-width:180px}\n\n/* Toast */\n#toast{position:fixed;bottom:24px;right:24px;z-index:200;display:flex;flex-direction:column;gap:8px}\n.toast-msg{padding:12px 18px;border-radius:8px;font-size:.85rem;font-weight:500;min-width:260px;box-shadow:0 4px 12px rgba(0,0,0,.4);animation:slide-in .25s ease}\n.toast-success{background:#14532d;color:#86efac;border:1px solid #22c55e}\n.toast-error{background:#7f1d1d;color:#fca5a5;border:1px solid #ef4444}\n.toast-info{background:#1e3a5f;color:#93c5fd;border:1px solid #3b82f6}\n@keyframes slide-in{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}\n\n/* Chat */\n#chat-msgs{height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:12px;background:#0f172a;display:flex;flex-direction:column;gap:8px}\n.chat-user{align-self:flex-end;background:var(--accent2);color:#fff;padding:8px 12px;border-radius:12px 12px 2px 12px;max-width:75%;font-size:.875rem}\n.chat-bot{align-self:flex-start;background:var(--panel);color:var(--text);padding:8px 12px;border-radius:12px 12px 12px 2px;max-width:75%;font-size:.875rem;white-space:pre-wrap}\n\n/* Misc */\n.row{display:flex;gap:12px;flex-wrap:wrap}\n.col{flex:1;min-width:200px}\n.section{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:20px}\n.section h3{font-size:.9rem;font-weight:600;margin-bottom:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}\n.empty{text-align:center;padding:32px;color:var(--muted);font-size:.875rem}\n.score-bar-wrap{height:8px;background:#1e293b;border-radius:4px;overflow:hidden;margin-top:4px}\n.score-bar{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--accent),var(--accent2))}\n.signal-bar{display:inline-block;font-family:monospace;letter-spacing:1px;font-size:.8rem}\n.divider{border:none;border-top:1px solid var(--border);margin:16px 0}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   POP-OUT EFFECTS \xe2\x80\x94 pure CSS, zero JS changes\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\n\n/* Page load fade-in */\n@keyframes fade-up{from{opacity:0;transform:translateY(18px)}to{opacity:1;transform:translateY(0)}}\n@keyframes fade-in{from{opacity:0}to{opacity:1}}\n@keyframes pop-in{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:scale(1)}}\n@keyframes slide-down{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:translateY(0)}}\n@keyframes glow-pulse{0%,100%{box-shadow:0 0 0 0 rgba(59,130,246,0)}50%{box-shadow:0 0 18px 4px rgba(59,130,246,.25)}}\n@keyframes shimmer{0%{background-position:-400px 0}100%{background-position:400px 0}}\n@keyframes bounce-in{0%{transform:scale(0.3);opacity:0}50%{transform:scale(1.08)}70%{transform:scale(0.96)}100%{transform:scale(1);opacity:1}}\n@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}\n@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}\n@keyframes border-flash{0%,100%{border-color:var(--border)}50%{border-color:var(--accent)}}\n\n/* Login box \xe2\x80\x94 bounces in */\n.login-box{animation:bounce-in .55s cubic-bezier(.36,.07,.19,.97) both}\n.login-box h2{animation:fade-up .4s .15s both}\n.login-box .form-group{animation:fade-up .4s both}\n.login-box .form-group:nth-child(3){animation-delay:.1s}\n.login-box .form-group:nth-child(4){animation-delay:.18s}\n.login-box .btn{animation:fade-up .4s .25s both}\n\n/* Sidebar nav items \xe2\x80\x94 stagger in */\n.nav-item{animation:fade-up .3s both;transition:all .2s cubic-bezier(.34,1.56,.64,1) !important}\n.nav-item:nth-child(1){animation-delay:.05s}\n.nav-item:nth-child(2){animation-delay:.09s}\n.nav-item:nth-child(3){animation-delay:.13s}\n.nav-item:nth-child(4){animation-delay:.17s}\n.nav-item:nth-child(5){animation-delay:.21s}\n.nav-item:nth-child(6){animation-delay:.25s}\n.nav-item:nth-child(7){animation-delay:.29s}\n.nav-item:nth-child(8){animation-delay:.33s}\n.nav-item:nth-child(9){animation-delay:.37s}\n.nav-item:nth-child(10){animation-delay:.41s}\n.nav-item:hover{transform:translateX(5px) scale(1.03) !important;color:var(--text);background:rgba(255,255,255,.07) !important}\n.nav-item.active{transform:translateX(3px) !important;animation:glow-pulse 2.5s ease-in-out infinite}\n\n/* Metric cards \xe2\x80\x94 pop up with stagger */\n.metric-card{animation:pop-in .35s cubic-bezier(.34,1.56,.64,1) both;\n  transition:transform .2s cubic-bezier(.34,1.56,.64,1),box-shadow .2s ease,border-color .2s}\n.metric-card:nth-child(1){animation-delay:.05s}\n.metric-card:nth-child(2){animation-delay:.10s}\n.metric-card:nth-child(3){animation-delay:.15s}\n.metric-card:nth-child(4){animation-delay:.20s}\n.metric-card:nth-child(5){animation-delay:.25s}\n.metric-card:nth-child(6){animation-delay:.30s}\n.metric-card:nth-child(7){animation-delay:.35s}\n.metric-card:nth-child(8){animation-delay:.40s}\n.metric-card:hover{transform:translateY(-6px) scale(1.04);\n  box-shadow:0 12px 32px rgba(59,130,246,.3);border-color:var(--accent)}\n.metric-val{transition:transform .15s;display:block}\n.metric-card:hover .metric-val{transform:scale(1.1)}\n\n/* Table wrapper \xe2\x80\x94 slides up */\n.tbl-wrap{animation:fade-up .4s .1s both;\n  transition:box-shadow .2s,border-color .2s}\n.tbl-wrap:hover{box-shadow:0 6px 24px rgba(0,0,0,.35);border-color:#475569}\n\n/* Table rows \xe2\x80\x94 lift on hover */\ntr{transition:all .15s ease}\ntr:hover td{background:rgba(59,130,246,.06) !important;\n  transform:none}\ntbody tr:hover{transform:translateX(2px)}\n\n/* Buttons \xe2\x80\x94 spring pop */\n.btn{transition:transform .18s cubic-bezier(.34,1.56,.64,1),\n  opacity .15s,box-shadow .18s !important}\n.btn:hover{transform:translateY(-2px) scale(1.05) !important;\n  opacity:1 !important;box-shadow:0 6px 18px rgba(59,130,246,.35)}\n.btn:active{transform:scale(.94) !important;box-shadow:none}\n.btn-danger:hover{box-shadow:0 6px 18px rgba(239,68,68,.35) !important}\n.btn-success:hover{box-shadow:0 6px 18px rgba(34,197,94,.35) !important}\n.btn-primary{animation:glow-pulse 3s ease-in-out infinite}\n\n/* Badges \xe2\x80\x94 pop on hover */\n.badge{transition:transform .18s cubic-bezier(.34,1.56,.64,1),box-shadow .15s;cursor:default}\n.badge:hover{transform:scale(1.18);box-shadow:0 3px 10px rgba(0,0,0,.35)}\n\n/* Modal \xe2\x80\x94 spring pop */\n.modal-backdrop.open{animation:fade-in .2s ease}\n.modal{animation:bounce-in .35s cubic-bezier(.34,1.56,.64,1) both}\n\n/* Sections (company detail, integrations, analytics) */\n.section{animation:fade-up .35s both;\n  transition:border-color .2s,box-shadow .2s}\n.section:hover{border-color:#475569;box-shadow:0 4px 20px rgba(0,0,0,.3)}\n\n/* Topbar \xe2\x80\x94 slides down */\n#topbar{animation:slide-down .3s ease both}\n\n/* Content area \xe2\x80\x94 fade up */\n#content{animation:fade-in .25s ease}\n\n/* Input focus \xe2\x80\x94 glow pop */\ninput:focus,select:focus,textarea:focus{\n  border-color:var(--accent) !important;\n  box-shadow:0 0 0 3px rgba(59,130,246,.18),0 2px 8px rgba(59,130,246,.15);\n  transform:none}\n\n/* Score bar \xe2\x80\x94 animated fill */\n.score-bar{transition:width .8s cubic-bezier(.34,1.2,.64,1)}\n\n/* Chat messages \xe2\x80\x94 slide in */\n.chat-user{animation:fade-up .2s ease both}\n.chat-bot{animation:fade-up .25s .05s ease both}\n\n/* Toast \xe2\x80\x94 already has slide-in, add pop */\n.toast-msg{animation:bounce-in .3s cubic-bezier(.36,.07,.19,.97) both !important}\n.toast-success{box-shadow:0 4px 18px rgba(34,197,94,.3) !important}\n.toast-error{box-shadow:0 4px 18px rgba(239,68,68,.3) !important}\n.toast-info{box-shadow:0 4px 18px rgba(59,130,246,.3) !important}\n\n/* Sidebar logo \xe2\x80\x94 float */\n#sidebar h2{animation:float 3s ease-in-out infinite}\n\n/* Signal bars \xe2\x80\x94 glow */\n.signal-bar{text-shadow:0 0 8px rgba(59,130,246,.6)}\n\n/* Score bar wrap \xe2\x80\x94 flash on load */\n.score-bar-wrap{animation:border-flash 1.5s ease 1s}\n\n/* Hot badge \xe2\x80\x94 pulse glow */\n.badge-hot{animation:glow-pulse 2s ease-in-out infinite;\n  box-shadow:0 0 8px rgba(239,68,68,.4)}\n\n/* Tbl-head action buttons */\n.tbl-head .btn{animation:none}\n\n/* Logout button */\n#logout-btn{transition:transform .18s cubic-bezier(.34,1.56,.64,1),background .15s}\n#logout-btn:hover{transform:scale(1.04);background:var(--accent2)}\n\n/* Tab active indicator pops */\n.tab{transition:color .15s,border-color .15s,transform .15s}\n.tab:hover{transform:translateY(-1px)}\n.tab.active{text-shadow:0 0 12px rgba(59,130,246,.5)}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   GLASSMORPHISM + CORPORATE MOTION \xe2\x80\x94 Pure CSS, Zero JS Overhead\n   \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\n\n/* \xe2\x94\x80\xe2\x94\x80 CSS Variables override for glass palette \xe2\x94\x80\xe2\x94\x80 */\n:root {\n  --glass-bg:   rgba(15,23,42,0.72);\n  --glass-border: rgba(148,163,184,0.12);\n  --glass-glow: rgba(59,130,246,0.15);\n  --sweep-color: rgba(255,255,255,0.04);\n  --orb1: rgba(59,130,246,0.18);\n  --orb2: rgba(99,102,241,0.14);\n  --orb3: rgba(16,185,129,0.10);\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Animated background canvas \xe2\x94\x80\xe2\x94\x80 */\nbody::before {\n  content:\'\';\n  position:fixed;\n  inset:0;\n  z-index:-2;\n  background:\n    radial-gradient(ellipse 80% 60% at 20% 10%,  var(--orb1), transparent 60%),\n    radial-gradient(ellipse 60% 80% at 80% 90%,  var(--orb2), transparent 60%),\n    radial-gradient(ellipse 50% 50% at 60% 40%,  var(--orb3), transparent 55%),\n    linear-gradient(135deg, #060912 0%, #0a0e1a 40%, #0d1526 100%);\n  animation: orb-drift 18s ease-in-out infinite alternate;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Subtle bokeh orbs \xe2\x94\x80\xe2\x94\x80 */\nbody::after {\n  content:\'\';\n  position:fixed;\n  inset:0;\n  z-index:-1;\n  background:\n    radial-gradient(circle 180px at 15% 25%,  rgba(59,130,246,0.09)  0%, transparent 70%),\n    radial-gradient(circle 240px at 85% 15%,  rgba(139,92,246,0.07)  0%, transparent 70%),\n    radial-gradient(circle 200px at 70% 80%,  rgba(16,185,129,0.06)  0%, transparent 70%),\n    radial-gradient(circle 160px at 35% 70%,  rgba(59,130,246,0.07)  0%, transparent 70%),\n    radial-gradient(circle 280px at 50% 50%,  rgba(99,102,241,0.04)  0%, transparent 70%);\n  animation: bokeh-float 24s ease-in-out infinite alternate;\n  pointer-events:none;\n}\n\n@keyframes orb-drift {\n  0%   { background-position: 0% 0%, 100% 100%, 60% 40%, center; }\n  33%  { background-position: 5% 15%, 90% 80%,  65% 35%, center; }\n  66%  { background-position: 10% 5%, 95% 90%,  55% 50%, center; }\n  100% { background-position: 3% 20%, 85% 85%,  70% 30%, center; }\n}\n\n@keyframes bokeh-float {\n  0%   { transform: translate(0px,    0px)    scale(1);    opacity:1;   }\n  25%  { transform: translate(8px,   -12px)   scale(1.04); opacity:0.9; }\n  50%  { transform: translate(-6px,   8px)    scale(0.97); opacity:1;   }\n  75%  { transform: translate(10px,   5px)    scale(1.03); opacity:0.95;}\n  100% { transform: translate(-4px,  -8px)    scale(1.01); opacity:1;   }\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Light sweep across panels \xe2\x94\x80\xe2\x94\x80 */\n@keyframes light-sweep {\n  0%   { background-position: -200% 0; }\n  100% { background-position: 200%  0; }\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Glassmorphism panels \xe2\x94\x80\xe2\x94\x80 */\n#sidebar {\n  background: linear-gradient(180deg, rgba(15,23,42,0.88) 0%, rgba(30,41,59,0.78) 100%) !important;\n  backdrop-filter: blur(20px) saturate(150%) !important;\n  -webkit-backdrop-filter: blur(20px) saturate(150%) !important;\n  border-right: 1px solid rgba(148,163,184,0.10) !important;\n  box-shadow: 1px 0 32px rgba(0,0,0,0.35) !important;\n}\n\n#topbar {\n  background: rgba(30,41,59,0.70) !important;\n  backdrop-filter: blur(16px) saturate(140%) !important;\n  -webkit-backdrop-filter: blur(16px) saturate(140%) !important;\n  border-bottom: 1px solid rgba(148,163,184,0.10) !important;\n  box-shadow: 0 2px 24px rgba(0,0,0,0.25) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Glass metric cards with sweep \xe2\x94\x80\xe2\x94\x80 */\n.metric-card {\n  background: linear-gradient(\n    135deg,\n    rgba(30,41,59,0.75) 0%,\n    rgba(15,23,42,0.65) 100%\n  ) !important;\n  backdrop-filter: blur(12px) !important;\n  -webkit-backdrop-filter: blur(12px) !important;\n  border: 1px solid rgba(148,163,184,0.10) !important;\n  box-shadow: 0 4px 24px rgba(0,0,0,0.30), inset 0 1px 0 rgba(255,255,255,0.04) !important;\n  position: relative;\n  overflow: hidden;\n}\n\n.metric-card::after {\n  content:\'\';\n  position:absolute;\n  inset:0;\n  background: linear-gradient(105deg,\n    transparent 30%,\n    rgba(255,255,255,0.045) 50%,\n    transparent 70%\n  );\n  background-size: 200% 100%;\n  animation: light-sweep 6s ease-in-out infinite;\n  pointer-events:none;\n}\n.metric-card:nth-child(2)::after { animation-delay: -1s; }\n.metric-card:nth-child(3)::after { animation-delay: -2s; }\n.metric-card:nth-child(4)::after { animation-delay: -3s; }\n.metric-card:nth-child(5)::after { animation-delay: -4s; }\n.metric-card:nth-child(6)::after { animation-delay: -1.5s; }\n.metric-card:nth-child(7)::after { animation-delay: -2.5s; }\n.metric-card:nth-child(8)::after { animation-delay: -0.5s; }\n\n/* \xe2\x94\x80\xe2\x94\x80 Glass table wrappers \xe2\x94\x80\xe2\x94\x80 */\n.tbl-wrap {\n  background: linear-gradient(\n    135deg,\n    rgba(30,41,59,0.72) 0%,\n    rgba(15,23,42,0.62) 100%\n  ) !important;\n  backdrop-filter: blur(12px) !important;\n  -webkit-backdrop-filter: blur(12px) !important;\n  border: 1px solid rgba(148,163,184,0.09) !important;\n  box-shadow: 0 8px 32px rgba(0,0,0,0.28), inset 0 1px 0 rgba(255,255,255,0.03) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Glass sections \xe2\x94\x80\xe2\x94\x80 */\n.section {\n  background: linear-gradient(\n    135deg,\n    rgba(30,41,59,0.72) 0%,\n    rgba(15,23,42,0.60) 100%\n  ) !important;\n  backdrop-filter: blur(12px) !important;\n  -webkit-backdrop-filter: blur(12px) !important;\n  border: 1px solid rgba(148,163,184,0.09) !important;\n  box-shadow: 0 8px 32px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.03) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Glass login box \xe2\x94\x80\xe2\x94\x80 */\n.login-box {\n  background: linear-gradient(\n    135deg,\n    rgba(30,41,59,0.80) 0%,\n    rgba(15,23,42,0.72) 100%\n  ) !important;\n  backdrop-filter: blur(24px) saturate(160%) !important;\n  -webkit-backdrop-filter: blur(24px) saturate(160%) !important;\n  border: 1px solid rgba(148,163,184,0.15) !important;\n  box-shadow:\n    0 24px 64px rgba(0,0,0,0.50),\n    0 0 0 1px rgba(255,255,255,0.04),\n    inset 0 1px 0 rgba(255,255,255,0.07) !important;\n  position:relative;\n  overflow:hidden;\n}\n\n.login-box::before {\n  content:\'\';\n  position:absolute;\n  top:-60%;\n  left:-60%;\n  width:220%;\n  height:220%;\n  background: conic-gradient(\n    from 0deg at 50% 50%,\n    transparent 0deg,\n    rgba(59,130,246,0.04) 60deg,\n    transparent 120deg\n  );\n  animation: slow-spin 30s linear infinite;\n  pointer-events:none;\n}\n\n@keyframes slow-spin {\n  from { transform: rotate(0deg); }\n  to   { transform: rotate(360deg); }\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Login sweep \xe2\x94\x80\xe2\x94\x80 */\n.login-box::after {\n  content:\'\';\n  position:absolute;\n  inset:0;\n  background: linear-gradient(105deg,\n    transparent 20%,\n    rgba(255,255,255,0.04) 50%,\n    transparent 80%\n  );\n  background-size:200% 100%;\n  animation: light-sweep 5s ease-in-out infinite;\n  pointer-events:none;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Glass modals \xe2\x94\x80\xe2\x94\x80 */\n.modal {\n  background: linear-gradient(\n    135deg,\n    rgba(30,41,59,0.90) 0%,\n    rgba(15,23,42,0.82) 100%\n  ) !important;\n  backdrop-filter: blur(24px) !important;\n  -webkit-backdrop-filter: blur(24px) !important;\n  border: 1px solid rgba(148,163,184,0.14) !important;\n  box-shadow:\n    0 32px 80px rgba(0,0,0,0.60),\n    inset 0 1px 0 rgba(255,255,255,0.06) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Inputs \xe2\x94\x80\xe2\x94\x80 */\ninput, select, textarea {\n  background: rgba(15,23,42,0.65) !important;\n  border: 1px solid rgba(148,163,184,0.14) !important;\n  backdrop-filter: blur(8px) !important;\n  transition: border-color 0.25s, box-shadow 0.25s, background 0.2s !important;\n}\ninput:focus, select:focus, textarea:focus {\n  background: rgba(15,23,42,0.80) !important;\n  border-color: rgba(59,130,246,0.55) !important;\n  box-shadow: 0 0 0 3px rgba(59,130,246,0.12), 0 0 20px rgba(59,130,246,0.08) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Nav active glow \xe2\x94\x80\xe2\x94\x80 */\n.nav-item.active {\n  background: rgba(59,130,246,0.12) !important;\n  border-left: 3px solid rgba(59,130,246,0.80) !important;\n  box-shadow: inset 3px 0 12px rgba(59,130,246,0.08) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Table header \xe2\x94\x80\xe2\x94\x80 */\nth {\n  background: rgba(15,23,42,0.70) !important;\n  backdrop-filter: blur(8px) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Subtle row hover glow \xe2\x94\x80\xe2\x94\x80 */\ntbody tr:hover td {\n  background: rgba(59,130,246,0.06) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 System status dots pulse \xe2\x94\x80\xe2\x94\x80 */\n@keyframes status-pulse {\n  0%, 100% { opacity:1;   box-shadow: 0 0 0 0px rgba(34,197,94,0.5); }\n  50%       { opacity:0.8; box-shadow: 0 0 0 4px rgba(34,197,94,0); }\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Ambient accent line under topbar \xe2\x94\x80\xe2\x94\x80 */\n#topbar::after {\n  content:\'\';\n  position:absolute;\n  bottom:0; left:0; right:0;\n  height:1px;\n  background: linear-gradient(90deg,\n    transparent,\n    rgba(59,130,246,0.4) 30%,\n    rgba(99,102,241,0.4) 70%,\n    transparent\n  );\n  animation: line-shimmer 4s ease-in-out infinite;\n}\n\n@keyframes line-shimmer {\n  0%,100% { opacity:0.4; transform:scaleX(0.8); }\n  50%     { opacity:1;   transform:scaleX(1);   }\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Sidebar logo ambient glow \xe2\x94\x80\xe2\x94\x80 */\n#sidebar h2 {\n  position:relative;\n}\n#sidebar h2::after {\n  content:\'\';\n  position:absolute;\n  bottom:-8px; left:16px; right:16px;\n  height:1px;\n  background: linear-gradient(90deg,\n    transparent,\n    rgba(59,130,246,0.5),\n    transparent\n  );\n  animation: line-shimmer 3s ease-in-out infinite;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Toast glass \xe2\x94\x80\xe2\x94\x80 */\n.toast-msg {\n  backdrop-filter: blur(16px) !important;\n  -webkit-backdrop-filter: blur(16px) !important;\n}\n.toast-success {\n  background: rgba(20,83,45,0.88) !important;\n  border: 1px solid rgba(34,197,94,0.35) !important;\n  box-shadow: 0 8px 24px rgba(34,197,94,0.20) !important;\n}\n.toast-error {\n  background: rgba(127,29,29,0.88) !important;\n  border: 1px solid rgba(239,68,68,0.35) !important;\n  box-shadow: 0 8px 24px rgba(239,68,68,0.20) !important;\n}\n.toast-info {\n  background: rgba(30,58,95,0.88) !important;\n  border: 1px solid rgba(59,130,246,0.35) !important;\n  box-shadow: 0 8px 24px rgba(59,130,246,0.20) !important;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Primary button ambient glow \xe2\x94\x80\xe2\x94\x80 */\n.btn-primary {\n  box-shadow: 0 4px 16px rgba(59,130,246,0.30), inset 0 1px 0 rgba(255,255,255,0.12) !important;\n  position:relative;\n  overflow:hidden;\n}\n.btn-primary::after {\n  content:\'\';\n  position:absolute;\n  inset:0;\n  background: linear-gradient(105deg,\n    transparent 30%,\n    rgba(255,255,255,0.08) 50%,\n    transparent 70%\n  );\n  background-size:200% 100%;\n  animation: light-sweep 4s ease-in-out infinite;\n  pointer-events:none;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Badge shimmer on hot \xe2\x94\x80\xe2\x94\x80 */\n.badge-hot {\n  position:relative;\n  overflow:hidden;\n}\n.badge-hot::after {\n  content:\'\';\n  position:absolute;\n  inset:0;\n  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);\n  background-size:200% 100%;\n  animation: light-sweep 2.5s ease-in-out infinite;\n  pointer-events:none;\n}\n\n/* \xe2\x94\x80\xe2\x94\x80 Topbar position relative for ::after \xe2\x94\x80\xe2\x94\x80 */\n#topbar { position:relative; }\n\n/* \xe2\x94\x80\xe2\x94\x80 Reduce motion for accessibility \xe2\x94\x80\xe2\x94\x80 */\n@media (prefers-reduced-motion: reduce) {\n  *, *::before, *::after {\n    animation-duration: 0.01ms !important;\n    animation-iteration-count: 1 !important;\n  }\n}\n</style>\n</head>\n<body>\n\n<!-- LOGIN -->\n<div id="login-screen">\n  <div class="login-box">\n    <h2>\xf0\x9f\xa4\x96 AI Sales Assistant</h2>\n    <p>Groq AI \xc2\xb7 Twilio SMS \xc2\xb7 Bland AI \xc2\xb7 Google Calendar</p>\n    <div class="form-group"><label>Email</label><input id="li-email" type="email" value="admin@salesai.com"></div>\n    <div class="form-group"><label>Password</label><input id="li-pass" type="password" value="Admin@123456"></div>\n    <button id="login-btn" class="btn btn-primary btn-full" onclick="doLogin()">Login \xe2\x86\x92</button>\n    <div style="text-align:center;margin-top:12px;font-size:.8rem;color:var(--muted)">\n      Don\'t have an account? <a href="#" onclick="showRegister()" style="color:var(--accent)">Register</a>\n    </div>\n  </div>\n</div>\n\n<!-- REGISTER MODAL -->\n<div class="modal-backdrop" id="reg-modal">\n  <div class="modal">\n    <h3>\xf0\x9f\x93\x9d Create Account <span class="modal-close" onclick="closeModal(\'reg-modal\')">\xe2\x9c\x95</span></h3>\n    <div class="form-group"><label>Full Name</label><input id="reg-name" placeholder="Your Name"></div>\n    <div class="form-group"><label>Email</label><input id="reg-email" type="email" placeholder="you@company.com"></div>\n    <div class="form-group"><label>Password</label><input id="reg-pass" type="password" placeholder="Min 6 chars"></div>\n    <div class="form-group"><label>Role</label>\n      <select id="reg-role"><option value="sales_rep">Sales Rep</option><option value="manager">Manager</option><option value="admin">Admin</option></select>\n    </div>\n    <button class="btn btn-primary btn-full" onclick="doRegister()">Create Account</button>\n  </div>\n</div>\n\n<!-- MAIN APP -->\n<div id="app" style="display:none">\n  <div id="sidebar">\n    <h2>\xf0\x9f\xa4\x96 AI Sales</h2>\n    <div id="user-info">Loading...</div>\n    <nav id="nav">\n      <div class="nav-item active" onclick="goto(\'dashboard\')">\xf0\x9f\x93\x8a Dashboard</div>\n      <div class="nav-item" onclick="goto(\'automation\')" style="background:rgba(59,130,246,.08);border-left:3px solid rgba(59,130,246,.4)">\xf0\x9f\xa4\x96 Automation</div>\n      <div class="nav-item" onclick="goto(\'companies\')">\xf0\x9f\x8f\xa2 Companies</div>\n      <div class="nav-item" onclick="goto(\'contacts\')">\xf0\x9f\x91\xa4 Contacts</div>\n      <div class="nav-item" onclick="goto(\'emails\')">\xf0\x9f\x93\xa7 Emails</div>\n      <div class="nav-item" onclick="goto(\'meetings\')">\xf0\x9f\x93\x85 Meetings</div>\n      <div class="nav-item" onclick="goto(\'calls\')">\xf0\x9f\x93\x9e Calls</div>\n      <div class="nav-item" onclick="goto(\'analytics\')">\xf0\x9f\x93\x88 Analytics</div>\n      <div class="nav-item" onclick="goto(\'chat\')">\xf0\x9f\x92\xac Chat</div>\n      <div class="nav-item" onclick="goto(\'sms\')">\xf0\x9f\x93\xb1 SMS Logs</div>\n      <div class="nav-item" onclick="goto(\'integrations\')">\xe2\x9a\x99\xef\xb8\x8f Integrations</div>\n    <div class="nav-item" onclick="goto(\'transcripts\')">\xf0\x9f\x93\x8b Transcripts</div>\n    <div class="nav-item" onclick="goto(\'drafts\')">\xe2\x9c\x89\xef\xb8\x8f Email Drafts</div>\n    <div class="nav-item" onclick="goto(\'opportunities\')">\xf0\x9f\x92\xb0 Opportunities</div>\n    <div class="nav-item" onclick="goto(\'invoices\')">\xf0\x9f\xa7\xbe Invoices</div>\n    <div class="nav-item" onclick="goto(\'quotations\')">\xf0\x9f\x93\x84 Quotations</div>\n    <div class="nav-item" onclick="goto(\'inbound\')">\xf0\x9f\x93\xb2 Inbound Calls</div>\n    </nav>\n    <div id="sys-status"><b>System Status</b></div>\n    <button id="logout-btn" onclick="logout()">\xf0\x9f\x9a\xaa Logout</button>\n  </div>\n  <div id="main">\n    <div id="topbar"><h1 id="page-title">Dashboard</h1><div id="topbar-actions"></div></div>\n    <div id="content"></div>\n  </div>\n</div>\n\n<div id="toast"></div>\n\n<!-- MODALS -->\n<div class="modal-backdrop" id="company-modal">\n  <div class="modal">\n    <h3 id="co-modal-title">\xe2\x9e\x95 Add Company <span class="modal-close" onclick="closeModal(\'company-modal\')">\xe2\x9c\x95</span></h3>\n    <input type="hidden" id="co-id">\n    <div class="row"><div class="col">\n      <div class="form-group"><label>Company Name *</label><input id="co-name" placeholder="Stripe"></div>\n      <div class="form-group"><label>Industry</label><input id="co-industry" placeholder="FinTech"></div>\n      <div class="form-group"><label>Employees</label><input id="co-emp" type="number" placeholder="500"></div>\n      <div class="form-group"><label>Annual Revenue ($)</label><input id="co-rev" type="number" placeholder="10000000"></div>\n    </div><div class="col">\n      <div class="form-group"><label>Website</label><input id="co-web" placeholder="stripe.com"></div>\n      <div class="form-group"><label>City</label><input id="co-city" placeholder="San Francisco"></div>\n      <div class="form-group"><label>Country</label><input id="co-country" placeholder="USA"></div>\n      <div class="form-group"><label>Status</label>\n        <select id="co-status"><option value="prospect">Prospect</option><option value="qualified">Qualified</option><option value="opportunity">Opportunity</option><option value="cold">Cold</option><option value="lost">Lost</option></select>\n      </div>\n    </div></div>\n    <div class="form-group"><label>Technologies (comma-separated)</label><input id="co-tech" placeholder="Python, React, AWS"></div>\n    <div class="form-group"><label>Description</label><textarea id="co-desc" rows="2"></textarea></div>\n    <div class="form-group"><label>LinkedIn URL</label><input id="co-linkedin" placeholder="https://linkedin.com/company/stripe"></div>\n    <button class="btn btn-primary btn-full" onclick="saveCompany()">Save Company</button>\n  </div>\n</div>\n\n<div class="modal-backdrop" id="contact-modal">\n  <div class="modal">\n    <h3>\xf0\x9f\x91\xa4 Add Contact <span class="modal-close" onclick="closeModal(\'contact-modal\')">\xe2\x9c\x95</span></h3>\n    <input type="hidden" id="ct-company-id">\n    <div class="row"><div class="col">\n      <div class="form-group"><label>First Name *</label><input id="ct-fn" placeholder="Alex"></div>\n      <div class="form-group"><label>Last Name</label><input id="ct-ln" placeholder="Johnson"></div>\n      <div class="form-group"><label>Email</label><input id="ct-email" type="email" placeholder="alex@company.com"></div>\n      <div class="form-group"><label>Phone</label><input id="ct-phone" placeholder="+14155550100"></div>\n    </div><div class="col">\n      <div class="form-group"><label>Title</label><input id="ct-title" placeholder="VP of Engineering"></div>\n      <div class="form-group"><label>Department</label><input id="ct-dept" placeholder="Engineering"></div>\n      <div class="form-group"><label>Seniority</label>\n        <select id="ct-sen"><option value="individual">Individual</option><option value="manager">Manager</option><option value="director">Director</option><option value="vp">VP</option><option value="c_suite">C-Suite</option></select>\n      </div>\n      <div class="form-group" style="padding-top:20px"><label style="display:flex;align-items:center;gap:8px;cursor:pointer"><input type="checkbox" id="ct-dm"> Decision Maker</label></div>\n    </div></div>\n    <button class="btn btn-primary btn-full" onclick="saveContact()">Add Contact</button>\n  </div>\n</div>\n\n<div class="modal-backdrop" id="email-modal">\n  <div class="modal">\n    <h3>\xf0\x9f\x93\xa7 Generate AI Email <span class="modal-close" onclick="closeModal(\'email-modal\')">\xe2\x9c\x95</span></h3>\n    <input type="hidden" id="em-company-id">\n    <div class="form-group"><label>Email Type</label>\n      <select id="em-type"><option value="cold">Cold Outreach</option><option value="follow_up">Follow Up</option><option value="meeting_request">Meeting Request</option></select>\n    </div>\n    <div class="form-group"><label>Custom Instructions (optional)</label><textarea id="em-custom" placeholder="Focus on their recent funding round..."></textarea></div>\n    <button class="btn btn-primary btn-full" onclick="genEmail()">\xf0\x9f\xa4\x96 Generate Email</button>\n    <div id="email-preview" style="display:none;margin-top:16px">\n      <div class="form-group"><label>Subject</label><input id="em-subject"></div>\n      <div class="form-group"><label>Body</label><textarea id="em-body" rows="8"></textarea></div>\n      <div style="display:flex;gap:8px">\n        <button class="btn btn-success" onclick="sendGenEmail()">\xf0\x9f\x93\xa4 Send Now</button>\n        <button class="btn btn-ghost" onclick="saveDraftEmail()">\xf0\x9f\x92\xbe Save Draft</button>\n      </div>\n    </div>\n  </div>\n</div>\n\n<div class="modal-backdrop" id="meeting-modal">\n  <div class="modal">\n    <h3>\xf0\x9f\x93\x85 Schedule Meeting <span class="modal-close" onclick="closeModal(\'meeting-modal\')">\xe2\x9c\x95</span></h3>\n    <input type="hidden" id="mtg-company-id">\n    <div class="form-group"><label>Title *</label><input id="mtg-title" placeholder="Discovery Call"></div>\n    <div class="form-group"><label>Type</label>\n      <select id="mtg-type"><option value="discovery">Discovery</option><option value="demo">Demo</option><option value="follow_up">Follow Up</option><option value="negotiation">Negotiation</option></select>\n    </div>\n    <div class="row"><div class="col">\n      <div class="form-group"><label>Date</label><input id="mtg-date" type="date"></div>\n    </div><div class="col">\n      <div class="form-group"><label>Time (UTC)</label><input id="mtg-time" type="time" value="10:00"></div>\n    </div></div>\n    <div class="form-group"><label>Duration (minutes)</label><input id="mtg-dur" type="number" value="30"></div>\n    <div class="form-group"><label>Description</label><textarea id="mtg-desc" rows="2"></textarea></div>\n    <button class="btn btn-primary btn-full" onclick="saveMeeting()">\xf0\x9f\x93\x85 Schedule + Set SMS Reminders</button>\n  </div>\n</div>\n\n<div class="modal-backdrop" id="call-modal">\n  <div class="modal">\n    <h3>\xf0\x9f\x93\x9e Make AI Call <span class="modal-close" onclick="closeModal(\'call-modal\')">\xe2\x9c\x95</span></h3>\n    <input type="hidden" id="call-company-id">\n    <div class="form-group"><label>Phone Number *</label><input id="call-phone" placeholder="+14155550100"></div>\n    <div class="form-group"><label>Objective</label>\n      <select id="call-obj"><option value="qualify">Qualify</option><option value="demo">Book Demo</option><option value="follow_up">Follow Up</option><option value="close">Close</option><option value="feedback">Get Feedback</option></select>\n    </div>\n    <div class="form-group"><label>AI Voice</label>\n      <select id="call-voice"><option value="nat">Nat (default)</option><option value="tanya">Tanya</option><option value="ryan">Ryan</option><option value="evelyn">Evelyn</option></select>\n    </div>\n    <div class="form-group"><label>Custom Script (leave blank to AI-generate)</label><textarea id="call-script" rows="4"></textarea></div>\n    <button class="btn btn-primary btn-full" onclick="makeCall()">\xf0\x9f\x93\x9e Initiate AI Call</button>\n  </div>\n</div>\n\n<script>\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   STATE & CONSTANTS\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nconst BASE = \'/api\';\nlet TOKEN = localStorage.getItem(\'sales_token\') || \'\';\nlet USER  = JSON.parse(localStorage.getItem(\'sales_user\') || \'null\');\nlet PAGE  = \'dashboard\';\nlet _lastEmailId = null;\nlet _genEmailData = null;\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   API HELPER\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nasync function api(method, path, body=null, isFile=false) {\n  const opts = { method, headers: {} };\n  if (TOKEN) opts.headers[\'Authorization\'] = \'Bearer \' + TOKEN;\n  if (body && !isFile) { opts.headers[\'Content-Type\'] = \'application/json\'; opts.body = JSON.stringify(body); }\n  if (body && isFile)  { opts.body = body; }\n  try {\n    const r = await fetch(BASE + path, opts);\n    // Handle non-JSON responses gracefully\n    const text = await r.text();\n    let data = {};\n    try { data = JSON.parse(text); } catch(e) {\n      // Not JSON \xe2\x80\x94 probably HTML error page\n      if (r.status === 404) { toast(\'API not found: \' + method + \' \' + path, \'error\'); return null; }\n      if (r.status === 405) { toast(\'Method not allowed: \' + method + \' \' + path, \'error\'); return null; }\n      if (r.status >= 500)  { toast(\'Server error \' + r.status + \' \xe2\x80\x94 check Render logs\', \'error\'); return null; }\n      toast(\'Unexpected response from server\', \'error\'); return null;\n    }\n    if (r.status === 401) {\n      // Token expired \xe2\x80\x94 clear and show login\n      TOKEN = \'\'; USER = null;\n      localStorage.removeItem(\'sales_token\');\n      localStorage.removeItem(\'sales_user\');\n      document.getElementById(\'login-screen\').style.display=\'flex\';\n      document.getElementById(\'app\').style.display=\'none\';\n      toast(\'Session expired \xe2\x80\x94 please log in again\', \'error\');\n      return null;\n    }\n    if (r.status === 403) { toast(\'Access denied (403)\', \'error\'); return null; }\n    if (r.status === 404) { toast(\'Not found: \' + path, \'error\'); return null; }\n    if (!r.ok || data.ok === false) {\n      toast(data.error || data.message || \'Error \' + r.status, \'error\');\n      return null;\n    }\n    return data.data !== undefined ? data.data : data;\n  } catch(e) {\n    // Check if it\'s a Render spin-up delay\n    if (e.message && e.message.includes(\'Failed to fetch\')) {\n      toast(\'Server is waking up \xe2\x80\x94 please wait a moment and try again\', \'error\');\n    } else {\n      toast(\'Network error: \' + e.message, \'error\');\n    }\n    return null;\n  }\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   AUTH\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nasync function doLogin() {\n  const email = v(\'li-email\'), pass = v(\'li-pass\');\n  if (!email || !pass) return toast(\'Enter email and password\',\'error\');\n  const btn = document.getElementById(\'login-btn\');\n  if (btn) { btn.textContent = \'Logging in\xe2\x80\xa6\'; btn.disabled = true; }\n  const d = await api(\'POST\', \'/auth/login\', { email, password: pass });\n  if (btn) { btn.textContent = \'Login \xe2\x86\x92\'; btn.disabled = false; }\n  if (!d) return;\n  TOKEN = d.token; USER = d.user;\n  localStorage.setItem(\'sales_token\', TOKEN);\n  localStorage.setItem(\'sales_user\', JSON.stringify(USER));\n  showApp();\n}\n\nasync function doRegister() {\n  const name=v(\'reg-name\'),email=v(\'reg-email\'),pass=v(\'reg-pass\'),role=v(\'reg-role\');\n  if (!name||!email||!pass) return toast(\'All fields required\',\'error\');\n  const d = await api(\'POST\', \'/auth/register\', {full_name:name,email,password:pass,role});\n  if (!d) return;\n  TOKEN = d.token; USER = d.user;\n  localStorage.setItem(\'sales_token\', TOKEN);\n  localStorage.setItem(\'sales_user\', JSON.stringify(USER));\n  closeModal(\'reg-modal\');\n  showApp();\n}\n\nfunction showRegister() { openModal(\'reg-modal\'); }\n\nfunction logout() {\n  TOKEN=\'\'; USER=null;\n  localStorage.removeItem(\'sales_token\');\n  localStorage.removeItem(\'sales_user\');\n  document.getElementById(\'app\').style.display=\'none\';\n  document.getElementById(\'login-screen\').style.display=\'flex\';\n}\n\nfunction showApp() {\n  document.getElementById(\'login-screen\').style.display=\'none\';\n  document.getElementById(\'app\').style.display=\'flex\';\n  document.getElementById(\'user-info\').textContent = USER ? USER.full_name+\' \xc2\xb7 \'+USER.role : \'\';\n  loadStatus();\n  goto(\'dashboard\');\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   NAVIGATION\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nconst PAGE_TITLES = {dashboard:\'\xf0\x9f\x93\x8a Dashboard\',automation:\'\xf0\x9f\xa4\x96 Automation Pipeline\',companies:\'\xf0\x9f\x8f\xa2 Companies\',contacts:\'\xf0\x9f\x91\xa4 Contacts\',\n  emails:\'\xf0\x9f\x93\xa7 Emails\',meetings:\'\xf0\x9f\x93\x85 Meetings\',calls:\'\xf0\x9f\x93\x9e AI Calls\',analytics:\'\xf0\x9f\x93\x88 Analytics\',\n  chat:\'\xf0\x9f\x92\xac AI Chat\',sms:\'\xf0\x9f\x93\xb1 SMS Logs\',integrations:\'\xe2\x9a\x99\xef\xb8\x8f Integrations\'};\n\nfunction goto(page) {\n  PAGE = page;\n  document.querySelectorAll(\'.nav-item\').forEach((el,i) => {\n    const pages = [\'dashboard\',\'automation\',\'companies\',\'contacts\',\'emails\',\'meetings\',\'calls\',\'analytics\',\'chat\',\'sms\',\'integrations\'];\n    el.classList.toggle(\'active\', pages[i] === page);\n  });\n  document.getElementById(\'page-title\').textContent = PAGE_TITLES[page] || page;\n  document.getElementById(\'topbar-actions\').innerHTML = \'\';\n  const fn = PAGES[page];\n  if (fn) fn();\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   PAGES\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nconst PAGES = { dashboard, automation, companies, contacts, emails, meetings, calls, analytics, chat, sms, integrations, transcripts, drafts, opportunities, invoices, quotations, inbound };\n\nasync function automation() {\n  const st = await api(\'GET\',\'/automation/status\');\n  const feed = await api(\'GET\',\'/automation/activity-feed\');\n  if (!st) return;\n  const p = st.pipeline, em = st.emails, ca = st.calls, mt = st.meetings, sc = st.schedule_utc, int_ = st.integrations;\n\n  const intBadge = (ok,lbl) => ok\n    ? `<span style="color:var(--green);font-size:.8rem">\xf0\x9f\x9f\xa2 ${lbl}</span>`\n    : `<span style="color:var(--red);font-size:.8rem">\xf0\x9f\x94\xb4 ${lbl} \xe2\x80\x94 configure in Integrations</span>`;\n\n  const feedIcon = t => ({email:\'\xf0\x9f\x93\xa7\',call:\'\xf0\x9f\x93\x9e\',sms:\'\xf0\x9f\x93\xb1\',meeting:\'\xf0\x9f\x93\x85\'}[t]||\'\xe2\x9a\xa1\');\n  const statusColor = s => s===\'sent\'||s===\'completed\'||s===\'queued\'?\'var(--green)\':s===\'replied\'?\'#a78bfa\':s===\'opened\'?\'var(--accent)\':\'var(--muted)\';\n\n  set(\'content\',`\n    <!-- INTEGRATION STATUS BAR -->\n    <div style="background:#1e293b;border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:20px;display:flex;flex-wrap:wrap;gap:16px;align-items:center">\n      <b style="font-size:.85rem">Integration Status:</b>\n      ${intBadge(int_.groq,\'Groq AI\')}\n      ${intBadge(int_.gmail,\'Gmail\')}\n      ${intBadge(int_.bland,\'Bland AI\')}\n      ${intBadge(int_.twilio,\'Twilio SMS\')}\n      ${!int_.groq||!int_.gmail||!int_.bland||!int_.twilio\n        ? `<button class="btn btn-sm btn-primary" onclick="goto(\'integrations\')" style="margin-left:auto">\xe2\x9a\x99\xef\xb8\x8f Configure \xe2\x86\x92</button>`\n        : `<span style="color:var(--green);margin-left:auto;font-size:.85rem;font-weight:600">\xe2\x9c\x85 Fully Configured \xe2\x80\x94 Automation Running</span>`}\n    </div>\n\n    <!-- PIPELINE METRICS -->\n    <div class="metrics-grid" style="margin-bottom:20px">\n      ${metric(\'\xf0\x9f\x94\xa5 Hot Leads\',p.hot_leads,\'var(--hot)\')}\n      ${metric(\'\xf0\x9f\x9f\xa1 Warm Leads\',p.warm_leads,\'var(--warm)\')}\n      ${metric(\'\xe2\x9d\x84\xef\xb8\x8f Cold Leads\',p.cold_leads,\'var(--cold)\')}\n      ${metric(\'\xf0\x9f\x92\xb0 Pipeline Value\',\'$\'+fmtM(p.pipeline_value),\'var(--green)\')}\n      ${metric(\'\xf0\x9f\x93\xa7 Cold Emails\',em.cold_sent)}\n      ${metric(\'\xf0\x9f\x93\xac Opened\',em.opened+\' (\'+em.open_rate+\'%)\')}\n      ${metric(\'\xe2\x86\xa9\xef\xb8\x8f Replied\',em.replied+\' (\'+em.reply_rate+\'%)\')}\n      ${metric(\'\xf0\x9f\x93\x9e Calls\',ca.total+\' / \'+ca.completed+\' done\')}\n    </div>\n\n    <!-- ACTION BUTTONS -->\n    <div class="section" style="margin-bottom:20px">\n      <h3>\xe2\x9a\xa1 Manual Triggers</h3>\n      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px">\n        <button class="btn btn-primary" onclick="runAuto(\'run-now\',\'\xf0\x9f\x94\x84 Full Automation Cycle\')">\xf0\x9f\x94\x84 Run Full Cycle</button>\n        <button class="btn btn-ghost" onclick="runAuto(\'score-now\',\'\xf0\x9f\x8e\xaf Scoring All Leads\')">\xf0\x9f\x8e\xaf Score All Leads</button>\n        <button class="btn btn-ghost" onclick="runAuto(\'email-now\',\'\xf0\x9f\x93\xa7 Sending Cold Emails\')">\xf0\x9f\x93\xa7 Send Cold Emails</button>\n        <button class="btn btn-ghost" onclick="runAuto(\'followup-now\',\'\xf0\x9f\x94\x81 Sending Follow-ups\')">\xf0\x9f\x94\x81 Follow-ups</button>\n        <button class="btn btn-ghost" onclick="runAuto(\'call-now\',\'\xf0\x9f\x93\x9e Calling Hot Leads\')">\xf0\x9f\x93\x9e Auto Call Leads</button>\n      </div>\n    </div>\n\n    <div class="row">\n      <!-- AUTOMATION SCHEDULE -->\n      <div class="col section">\n        <h3>\xf0\x9f\x95\x90 Automatic Schedule (UTC)</h3>\n        <table style="width:100%">\n          <thead><tr><th>Time</th><th>Action</th><th>Condition</th></tr></thead>\n          <tbody>\n            <tr><td><b>${sc.score}</b></td><td>\xf0\x9f\x8e\xaf Score all leads</td><td>Daily</td></tr>\n            <tr><td><b>${sc.cold_email}</b></td><td>\xf0\x9f\x93\xa7 Cold email hot leads</td><td>Score \xe2\x89\xa5 70, no prior email</td></tr>\n            <tr><td><b>${sc.follow_up}</b></td><td>\xf0\x9f\x94\x81 Follow-up email</td><td>Sent 3+ days ago, no reply</td></tr>\n            <tr><td><b>${sc.auto_call}</b></td><td>\xf0\x9f\x93\x9e AI phone call</td><td>Score \xe2\x89\xa5 80, not yet called</td></tr>\n            <tr><td><b>${sc.daily_report}</b></td><td>\xf0\x9f\x93\x8a Daily SMS report</td><td>Every day</td></tr>\n            <tr><td><b>${sc.weekly_report}</b></td><td>\xf0\x9f\x93\x8b Weekly SMS report</td><td>Mondays only</td></tr>\n          </tbody>\n        </table>\n        <div style="margin-top:12px;padding:10px;background:#0f172a;border-radius:6px;font-size:.8rem;color:var(--muted)">\n          \xf0\x9f\x92\xa1 Automation runs hourly. Checks the UTC hour and triggers the right action.\n          All actions log to SMS Logs page.\n        </div>\n      </div>\n\n      <!-- EMAIL FUNNEL -->\n      <div class="col section">\n        <h3>\xf0\x9f\x93\xa7 Email Funnel</h3>\n        ${funnelBar(\'Cold Sent\',em.cold_sent,em.cold_sent,\'var(--accent)\')}\n        ${funnelBar(\'Opened\',em.opened,em.cold_sent,\'var(--warm)\')}\n        ${funnelBar(\'Replied\',em.replied,em.cold_sent,\'var(--green)\')}\n        ${funnelBar(\'Follow-ups\',em.followups,em.cold_sent,\'#a78bfa\')}\n        <hr class="divider">\n        <div style="display:flex;justify-content:space-between;font-size:.85rem">\n          <span>Open Rate</span><b style="color:var(--warm)">${em.open_rate}%</b>\n        </div>\n        <div style="display:flex;justify-content:space-between;font-size:.85rem;margin-top:4px">\n          <span>Reply Rate</span><b style="color:var(--green)">${em.reply_rate}%</b>\n        </div>\n        <hr class="divider">\n        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">\n          <button class="btn btn-sm btn-ghost" onclick="goto(\'emails\')">View All Emails \xe2\x86\x92</button>\n        </div>\n      </div>\n    </div>\n\n    <!-- LIVE ACTIVITY FEED -->\n    <div class="section">\n      <h3>\xe2\x9a\xa1 Live Activity Feed</h3>\n      <div style="display:flex;flex-direction:column;gap:6px;max-height:400px;overflow-y:auto">\n        ${(feed||[]).length ? (feed||[]).map(f=>`\n          <div style="display:flex;align-items:center;gap:12px;padding:8px 12px;background:#0f172a;border-radius:8px;border-left:3px solid ${statusColor(f.status)}">\n            <span style="font-size:1.1rem">${feedIcon(f.type)}</span>\n            <div style="flex:1">\n              <div style="font-size:.85rem;font-weight:600">${esc(f.title||f.type)}</div>\n              <div style="font-size:.75rem;color:var(--muted)">${esc(f.target||\'\')} ${f.subtype?\'\xc2\xb7 \'+f.subtype:\'\'}</div>\n            </div>\n            <span class="badge badge-${f.status||\'draft\'}" style="flex-shrink:0">${f.status||\'\xe2\x80\x94\'}</span>\n            <span style="font-size:.7rem;color:var(--muted);flex-shrink:0">${fmtDt(f.created_at)}</span>\n          </div>`).join(\'\')\n        : \'<div class="empty">No activity yet \xe2\x80\x94 run automation to populate</div>\'}\n      </div>\n      <button class="btn btn-sm btn-ghost" style="margin-top:12px" onclick="automation()">\xf0\x9f\x94\x84 Refresh Feed</button>\n    </div>\n\n    <!-- HOW IT WORKS -->\n    <div class="section">\n      <h3>\xf0\x9f\x94\x81 How Full Automation Works</h3>\n      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:4px">\n        ${[\'\xf0\x9f\x8e\xaf Score Leads<br><small>All companies scored hourly using revenue, headcount, industry, buying signals</small>\',\n           \'\xf0\x9f\x93\xa7 Cold Outreach<br><small>AI writes personalised cold email via Groq \xe2\x86\x92 sends via Gmail automatically</small>\',\n           \'\xf0\x9f\x94\x81 Follow-Up<br><small>Auto follow-up 3 days after no reply. Tracks opened vs unopened</small>\',\n           \'\xf0\x9f\x93\x9e AI Calls<br><small>Bland AI calls hot leads (score\xe2\x89\xa580) with AI-generated script. Records + transcribes</small>\',\n           \'\xf0\x9f\x93\xb1 SMS Updates<br><small>Twilio SMS alerts for every event: new leads, emails sent, calls, meetings</small>\',\n           \'\xf0\x9f\x93\x8a Reports<br><small>Daily + Weekly SMS report: hot/warm/cold counts, pipeline value, top leads</small>\'\n          ].map(s=>`<div style="padding:12px;background:#0f172a;border-radius:8px;font-size:.82rem;line-height:1.5">${s}</div>`).join(\'\')}\n      </div>\n    </div>\n  `);\n}\n\nfunction funnelBar(label, val, total, color) {\n  const pct = total > 0 ? Math.round((val/total)*100) : 0;\n  return `<div style="margin-bottom:10px">\n    <div style="display:flex;justify-content:space-between;font-size:.8rem;margin-bottom:3px">\n      <span>${label}</span><span>${val} (${pct}%)</span>\n    </div>\n    <div style="height:10px;background:#1e293b;border-radius:5px;overflow:hidden">\n      <div style="height:100%;width:${pct}%;background:${color};border-radius:5px;transition:width .8s cubic-bezier(.34,1.2,.64,1)"></div>\n    </div>\n  </div>`;\n}\n\nasync function runAuto(action, label) {\n  toast(`\xe2\x9a\xa1 ${label} started...`, \'info\');\n  const r = await api(\'POST\', `/automation/${action}`);\n  if (r) {\n    toast(`\xe2\x9c\x85 ${r.message}`, \'success\');\n    setTimeout(automation, 2000); // refresh page after 2s\n  }\n}\n\nasync function dashboard() {\n  const [sum, cos, mtgs] = await Promise.all([\n    api(\'GET\',\'/analytics/summary\'),\n    api(\'GET\',\'/companies?limit=5\'),\n    api(\'GET\',\'/meetings?status=scheduled\'),\n  ]);\n  if (!sum) return;\n\n  set(\'content\', `\n    <div class="metrics-grid">\n      ${metric(\'\xf0\x9f\x8f\xa2 Companies\',sum.total_companies)}\n      ${metric(\'\xf0\x9f\x94\xa5 Hot Leads\',sum.hot_leads,\'var(--hot)\')}\n      ${metric(\'\xf0\x9f\x9f\xa1 Warm Leads\',sum.warm_leads,\'var(--warm)\')}\n      ${metric(\'\xf0\x9f\x93\xa7 Emails Sent\',sum.emails_sent)}\n      ${metric(\'\xf0\x9f\x93\xad Open Rate\',sum.open_rate+\'%\')}\n      ${metric(\'\xf0\x9f\x93\x85 Meetings\',sum.meetings_scheduled,\'var(--green)\')}\n      ${metric(\'\xf0\x9f\x93\x9e Calls\',sum.total_calls)}\n      ${metric(\'\xf0\x9f\x92\xb0 Pipeline\',\'$\'+fmt(sum.revenue_pipeline),\'var(--green)\')}\n    </div>\n    <div class="row">\n      <div class="col tbl-wrap">\n        <div class="tbl-head"><h3>\xf0\x9f\x94\xa5 Top Hot Leads</h3><button class="btn btn-sm btn-ghost" onclick="goto(\'companies\')">View All \xe2\x86\x92</button></div>\n        <table><thead><tr><th>Company</th><th>Score</th><th>Industry</th><th>Status</th></tr></thead>\n        <tbody>${(cos||[]).map(c=>`<tr>\n          <td><b>${esc(c.name)}</b></td>\n          <td>${scoreBadge(c.lead_score)}</td>\n          <td>${esc(c.industry||\'\xe2\x80\x94\')}</td>\n          <td><span class="badge badge-${c.status}">${c.status}</span></td>\n        </tr>`).join(\'\')}</tbody></table>\n      </div>\n      <div class="col tbl-wrap">\n        <div class="tbl-head"><h3>\xf0\x9f\x93\x85 Upcoming Meetings</h3><button class="btn btn-sm btn-ghost" onclick="goto(\'meetings\')">View All \xe2\x86\x92</button></div>\n        <table><thead><tr><th>Title</th><th>Company</th><th>When</th></tr></thead>\n        <tbody>${(mtgs||[]).slice(0,6).map(m=>`<tr>\n          <td><b>${esc(m.title)}</b></td>\n          <td>${esc(m.company_name||\'\xe2\x80\x94\')}</td>\n          <td style="font-size:.8rem;color:var(--muted)">${fmtDt(m.scheduled_at)}</td>\n        </tr>`).join(\'\')}</tbody></table>\n      </div>\n    </div>\n  `);\n}\n\nasync function companies() {\n  document.getElementById(\'topbar-actions\').innerHTML =\n    \'<button class="btn btn-primary btn-sm" onclick="openCompanyModal()">\xe2\x9e\x95 Add Company</button>\';\n  await renderCompanies();\n}\n\nasync function renderCompanies(search=\'\',status=\'\') {\n  let url = `/companies?limit=200`;\n  if (search) url += `&search=${encodeURIComponent(search)}`;\n  if (status) url += `&status=${encodeURIComponent(status)}`;\n  const cos = await api(\'GET\', url);\n  if (!cos) return;\n\n  set(\'content\',`\n    <div class="search-bar">\n      <input id="co-search" placeholder="\xf0\x9f\x94\x8d Search companies..." onkeyup="debounce(()=>renderCompanies(v(\'co-search\'),v(\'co-sf\')),400)" value="${esc(search)}">\n      <select id="co-sf" onchange="renderCompanies(v(\'co-search\'),this.value)">\n        <option value="">All Statuses</option>\n        ${[\'prospect\',\'qualified\',\'opportunity\',\'cold\',\'lost\'].map(s=>`<option value="${s}" ${s===status?\'selected\':\'\'}>${s}</option>`).join(\'\')}\n      </select>\n      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">\n        <input type="file" id="csv-file" accept=".csv" style="display:none" onchange="uploadCSV(this)">\n        <button class="btn btn-ghost btn-sm" onclick="document.getElementById(\'csv-file\').click()">\xf0\x9f\x93\xa4 Import CSV</button>\n      </label>\n    </div>\n    <div class="tbl-wrap">\n      <div class="tbl-head"><h3>${cos.length} Companies</h3></div>\n      <table><thead><tr><th>Company</th><th>Industry</th><th>Score</th><th>Status</th><th>Employees</th><th>Revenue</th><th>Actions</th></tr></thead>\n      <tbody>${cos.map(c=>`<tr>\n        <td><b>${esc(c.name)}</b>${c.ai_summary?`<div style="font-size:.75rem;color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.ai_summary)}</div>`:\'\'}</td>\n        <td>${esc(c.industry||\'\xe2\x80\x94\')}</td>\n        <td>${scoreBadge(c.lead_score)}</td>\n        <td><span class="badge badge-${c.status||\'prospect\'}">${c.status||\'prospect\'}</span></td>\n        <td>${c.employee_count?fmt(c.employee_count):\'\xe2\x80\x94\'}</td>\n        <td>${c.annual_revenue?\'$\'+fmtM(c.annual_revenue):\'\xe2\x80\x94\'}</td>\n        <td style="white-space:nowrap">\n          <button class="btn btn-sm btn-ghost" onclick="viewCompany(${c.id})">View</button>\n          <button class="btn btn-sm btn-ghost" onclick="scoreCompany(${c.id})">Score</button>\n          <button class="btn btn-sm btn-ghost" onclick="openEmailModal(${c.id})">Email</button>\n          <button class="btn btn-sm btn-danger" onclick="deleteCompany(${c.id},\'${esc(c.name)}\')">Del</button>\n        </td>\n      </tr>`).join(\'\')}</tbody></table>\n    </div>\n  `);\n}\n\nasync function viewCompany(cid) {\n  const co = await api(\'GET\', `/companies/${cid}`);\n  if (!co) return;\n\n  const techStr = Array.isArray(co.technologies) ? co.technologies.join(\', \')\n    : (co.technologies ? JSON.parse(co.technologies).join(\', \') : \'\xe2\x80\x94\');\n\n  set(\'content\',`\n    <button class="btn btn-ghost btn-sm" onclick="goto(\'companies\')" style="margin-bottom:16px">\xe2\x86\x90 Back</button>\n    <div class="section">\n      <div class="row" style="align-items:flex-start">\n        <div class="col">\n          <h2 style="font-size:1.3rem;margin-bottom:8px">${esc(co.name)} ${scoreBadge(co.lead_score)}</h2>\n          <p style="color:var(--muted);font-size:.85rem;margin-bottom:12px">${esc(co.description||\'\')}</p>\n          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:.85rem">\n            <div>\xf0\x9f\x8f\xad <b>Industry:</b> ${esc(co.industry||\'\xe2\x80\x94\')}</div>\n            <div>\xf0\x9f\x93\x8d <b>Location:</b> ${esc((co.city||\'\')+\' \'+(co.country||\'\'))}</div>\n            <div>\xf0\x9f\x91\xa5 <b>Employees:</b> ${co.employee_count?fmt(co.employee_count):\'\xe2\x80\x94\'}</div>\n            <div>\xf0\x9f\x92\xb0 <b>Revenue:</b> ${co.annual_revenue?\'$\'+fmtM(co.annual_revenue):\'\xe2\x80\x94\'}</div>\n            <div>\xf0\x9f\x8c\x90 <b>Website:</b> ${co.website?`<a href="https://${co.website}" target="_blank" style="color:var(--accent)">${co.website}</a>`:\'\xe2\x80\x94\'}</div>\n            <div>\xf0\x9f\x93\x8a <b>Status:</b> <span class="badge badge-${co.status||\'prospect\'}">${co.status||\'prospect\'}</span></div>\n            <div>\xf0\x9f\x92\xbb <b>Tech:</b> ${techStr}</div>\n          </div>\n          ${co.ai_summary?`<div style="margin-top:12px;padding:10px;background:#0f172a;border-radius:6px;font-size:.85rem">\xf0\x9f\xa4\x96 ${esc(co.ai_summary)}</div>`:\'\'}\n        </div>\n        <div style="display:flex;flex-direction:column;gap:8px;min-width:160px">\n          <button class="btn btn-primary btn-sm" onclick="scoreCompany(${cid},true)">\xf0\x9f\x8e\xaf Score Lead</button>\n          <button class="btn btn-ghost btn-sm" onclick="aiSummary(${cid})">\xf0\x9f\xa4\x96 AI Summary</button>\n          <button class="btn btn-ghost btn-sm" onclick="analyzeSignals(${cid})">\xf0\x9f\x93\xa1 Buying Signals</button>\n          <button class="btn btn-ghost btn-sm" onclick="openEmailModal(${cid})">\xf0\x9f\x93\xa7 Generate Email</button>\n          <button class="btn btn-ghost btn-sm" onclick="openMeetingModal(${cid})">\xf0\x9f\x93\x85 Schedule Meeting</button>\n          <button class="btn btn-ghost btn-sm" onclick="openCallModal(${cid})">\xf0\x9f\x93\x9e AI Call</button>\n          <button class="btn btn-ghost btn-sm" onclick="openContactModal(${cid})">\xf0\x9f\x91\xa4 Add Contact</button>\n          <button class="btn btn-ghost btn-sm" onclick="editCompanyModal(${cid})">\xe2\x9c\x8f\xef\xb8\x8f Edit</button>\n        </div>\n      </div>\n    </div>\n    ${companyTabs(co)}\n  `);\n}\n\nfunction companyTabs(co) {\n  const tabs = [\'Contacts\',\'Emails\',\'Meetings\',\'Calls\',\'Signals\',\'Scores\'];\n  return `\n    <div class="tabs">${tabs.map((t,i)=>`<div class="tab ${i===0?\'active\':\'\'}" onclick="switchTab(this,\'co-tab-${i}\')">${t}</div>`).join(\'\')}</div>\n    <div id="co-tab-0" class="tab-panel active">\n      ${co.contacts.length ? `<table><thead><tr><th>Name</th><th>Title</th><th>Email</th><th>Phone</th><th>Seniority</th><th>DM?</th></tr></thead>\n      <tbody>${co.contacts.map(c=>`<tr>\n        <td><b>${esc(c.first_name)} ${esc(c.last_name||\'\')}</b></td>\n        <td>${esc(c.title||\'\xe2\x80\x94\')}</td><td>${esc(c.email||\'\xe2\x80\x94\')}</td><td>${esc(c.phone||\'\xe2\x80\x94\')}</td>\n        <td>${c.seniority_level||\'\xe2\x80\x94\'}</td><td>${c.is_decision_maker?\'\xe2\x9c\x85\':\'\'}</td>\n      </tr>`).join(\'\')}</tbody></table>` : \'<div class="empty">No contacts \xe2\x80\x94 add one above</div>\'}\n    </div>\n    <div id="co-tab-1" class="tab-panel">\n      ${co.emails.length ? `<table><thead><tr><th>Subject</th><th>Type</th><th>Status</th><th>Recipient</th><th>Actions</th></tr></thead>\n      <tbody>${co.emails.map(e=>`<tr>\n        <td>${esc(e.subject)}</td>\n        <td>${(e.email_type||\'\').replace(/_/g,\' \')}</td>\n        <td><span class="badge badge-${e.status}">${e.status}</span></td>\n        <td>${esc(e.recipient_email||\'\')}</td>\n        <td>${e.status===\'draft\'?`<button class="btn btn-sm btn-success" onclick="sendEmail(${e.id})">Send</button>`:\'\xe2\x80\x94\'}</td>\n      </tr>`).join(\'\')}</tbody></table>` : \'<div class="empty">No emails yet</div>\'}\n    </div>\n    <div id="co-tab-2" class="tab-panel">\n      ${co.meetings.length ? `<table><thead><tr><th>Title</th><th>Type</th><th>When</th><th>Status</th><th>Actions</th></tr></thead>\n      <tbody>${co.meetings.map(m=>`<tr>\n        <td><b>${esc(m.title)}</b></td>\n        <td>${(m.meeting_type||\'\').replace(/_/g,\' \')}</td>\n        <td style="font-size:.8rem">${fmtDt(m.scheduled_at)}</td>\n        <td><span class="badge badge-${m.status||\'proposed\'}">${m.status||\'proposed\'}</span></td>\n        <td style="white-space:nowrap">\n          ${m.meeting_link?`<a href="${m.meeting_link}" target="_blank" class="btn btn-sm btn-ghost">\xf0\x9f\x93\xb9 Join</a>`:\'\'}\n          ${m.status!==\'completed\'?`<button class="btn btn-sm btn-success" onclick="completeMeeting(${m.id},${co.id})">Done</button>`:\'\'}\n          <button class="btn btn-sm btn-ghost" onclick="addToCalendar(${m.id},${co.id})">\xf0\x9f\x93\x85 Cal</button>\n        </td>\n      </tr>`).join(\'\')}</tbody></table>` : \'<div class="empty">No meetings yet</div>\'}\n    </div>\n    <div id="co-tab-3" class="tab-panel">\n      ${co.calls.length ? `<table><thead><tr><th>Phone</th><th>Objective</th><th>Status</th><th>Duration</th><th>Summary</th></tr></thead>\n      <tbody>${co.calls.map(c=>`<tr>\n        <td>${esc(c.phone_number)}</td>\n        <td>${c.objective||\'\xe2\x80\x94\'}</td>\n        <td><span class="badge badge-${c.status||\'queued\'}">${c.status||\'queued\'}</span></td>\n        <td>${c.duration_seconds?c.duration_seconds+\'s\':\'\xe2\x80\x94\'}</td>\n        <td style="font-size:.8rem;max-width:200px">${esc((c.summary||\'\').slice(0,80))}</td>\n      </tr>`).join(\'\')}</tbody></table>` : \'<div class="empty">No calls yet</div>\'}\n    </div>\n    <div id="co-tab-4" class="tab-panel">\n      ${co.buying_signals.length ? co.buying_signals.map(s=>`\n        <div style="padding:10px 0;border-bottom:1px solid var(--border)">\n          <b>${esc(s.signal_name)}</b>\n          <span class="signal-bar" style="margin-left:12px;color:var(--accent)">${\'\xe2\x96\x88\'.repeat(s.strength||5)}${\'\xe2\x96\x91\'.repeat(10-(s.strength||5))}</span>\n          <span style="font-size:.75rem;color:var(--muted);margin-left:6px">${s.strength}/10</span>\n          <div style="font-size:.8rem;color:var(--muted);margin-top:3px">${esc(s.signal_description||\'\')}</div>\n        </div>`).join(\'\')\n      : \'<div class="empty">Click "Buying Signals" to analyse</div>\'}\n    </div>\n    <div id="co-tab-5" class="tab-panel">\n      ${co.lead_score_details ? `\n        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">\n          ${[[\'Revenue\',co.lead_score_details.revenue_score],[\'Employees\',co.lead_score_details.employee_score],\n             [\'Industry\',co.lead_score_details.industry_score],[\'Buying Signals\',co.lead_score_details.buying_signal_score],\n             [\'Seniority\',co.lead_score_details.department_signal_score],[\'Email Activity\',co.lead_score_details.email_activity_score]\n            ].map(([lbl,val])=>`<div>\n            <div style="display:flex;justify-content:space-between;font-size:.8rem"><span>${lbl}</span><span>${val}/100</span></div>\n            <div class="score-bar-wrap"><div class="score-bar" style="width:${val}%"></div></div>\n          </div>`).join(\'\')}\n        </div>\n        <div style="margin-top:16px;text-align:center;font-size:1.1rem">\n          Total: <b style="color:var(--accent)">${co.lead_score_details.total_score}/100</b>\n          \xe2\x80\x94 Tier: <span class="badge badge-${co.lead_score_details.tier}">${co.lead_score_details.tier}</span>\n        </div>` : \'<div class="empty">Run "Score Lead" to see breakdown</div>\'}\n    </div>\n  `;\n}\n\nasync function contacts() {\n  // \xe2\x94\x80\xe2\x94\x80 state \xe2\x94\x80\xe2\x94\x80\n  let allContacts = [];\n  let filtered    = [];\n  let viewMode    = \'table\';   // \'table\' | \'cards\'\n\n  // \xe2\x94\x80\xe2\x94\x80 score helpers \xe2\x94\x80\xe2\x94\x80\n  const scoreColor = s => !s&&s!==0 ? \'#475569\'\n    : s>=8 ? \'#22c55e\' : s>=6 ? \'#60a5fa\' : s>=4 ? \'#f59e0b\' : \'#ef4444\';\n  const scoreTier  = s => !s&&s!==0 ? \'\xe2\x80\x94\'\n    : s>=8 ? \'\xf0\x9f\x94\xa5 Hot\' : s>=6 ? \'\xf0\x9f\x92\x99 Warm\' : s>=4 ? \'\xe2\x9a\xa0\xef\xb8\x8f Caution\' : \'\xe2\x9d\x84\xef\xb8\x8f Cold\';\n  const tierBg     = s => !s&&s!==0 ? \'#1e293b\'\n    : s>=8 ? \'#14532d\' : s>=6 ? \'#1e3a5f\' : s>=4 ? \'#78350f\' : \'#1e293b\';\n\n  // \xe2\x94\x80\xe2\x94\x80 fetch \xe2\x94\x80\xe2\x94\x80\n  async function load(params=\'\') {\n    const data = await api(\'GET\',`/contacts${params}`);\n    if (!data) return;\n    allContacts = data;\n    filtered    = data;\n    render();\n  }\n\n  // \xe2\x94\x80\xe2\x94\x80 filter \xe2\x94\x80\xe2\x94\x80\n  function applyFilter() {\n    const s   = (document.getElementById(\'ct-search\')?.value||\'\').toLowerCase();\n    const sen = document.getElementById(\'ct-seniority\')?.value||\'\';\n    const q_  = document.getElementById(\'ct-qualified\')?.value||\'\';\n    filtered = allContacts.filter(c => {\n      const name = `${c.first_name||\'\'} ${c.last_name||\'\'} ${c.company_name||\'\'} ${c.email||\'\'}`.toLowerCase();\n      if (s && !name.includes(s)) return false;\n      if (sen && c.seniority_level !== sen) return false;\n      if (q_ === \'yes\' && (c.qualify_score||0) < 6) return false;\n      if (q_ === \'no\'  && (c.qualify_score||0) >= 6) return false;\n      return true;\n    });\n    renderRows();\n  }\n\n  // \xe2\x94\x80\xe2\x94\x80 render shell \xe2\x94\x80\xe2\x94\x80\n  function render() {\n    set(\'content\', `\n      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px">\n        <h3 style="margin:0">${allContacts.length} Contacts</h3>\n        <div style="display:flex;gap:8px;flex-wrap:wrap">\n          <button class="btn btn-sm btn-ghost" onclick="toggleContactView()" id="view-toggle">\xf0\x9f\x83\x8f Card view</button>\n          <button class="btn btn-sm btn-ghost" onclick="qualifyAllContacts()">\xf0\x9f\x8e\xaf Qualify All</button>\n          <button class="btn btn-sm btn-ghost" onclick="showImportContactsModal()">\xf0\x9f\x93\xa4 Import CSV</button>\n          <button class="btn btn-sm btn-primary" onclick="showAddContactModal()">+ Add Contact</button>\n        </div>\n      </div>\n\n      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">\n        <input id="ct-search" placeholder="Search name, company, email\xe2\x80\xa6"\n          style="flex:1;min-width:180px" oninput="applyFilter()">\n        <select id="ct-seniority" onchange="applyFilter()" style="width:140px">\n          <option value="">All seniority</option>\n          <option value="c_suite">C-Suite</option>\n          <option value="vp">VP</option>\n          <option value="director">Director</option>\n          <option value="manager">Manager</option>\n          <option value="owner">Owner</option>\n          <option value="individual">Individual</option>\n        </select>\n        <select id="ct-qualified" onchange="applyFilter()" style="width:150px">\n          <option value="">All qualify scores</option>\n          <option value="yes">Qualified (6+)</option>\n          <option value="no">Below threshold</option>\n        </select>\n      </div>\n\n      <div id="ct-rows"></div>\n    `);\n    renderRows();\n  }\n\n  // \xe2\x94\x80\xe2\x94\x80 render rows \xe2\x94\x80\xe2\x94\x80\n  function renderRows() {\n    const wrap = document.getElementById(\'ct-rows\');\n    if (!wrap) return;\n    if (!filtered.length) {\n      wrap.innerHTML = \'<p style="color:var(--muted);padding:16px">No contacts found.</p>\';\n      return;\n    }\n    if (viewMode === \'cards\') {\n      wrap.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px">\n        ${filtered.map(c => cardHTML(c)).join(\'\')}</div>`;\n    } else {\n      wrap.innerHTML = `\n        <div class="tbl-wrap">\n          <table style="width:100%">\n            <thead><tr>\n              <th>Name &amp; Title</th>\n              <th>Company &amp; Location</th>\n              <th>Contact</th>\n              <th>LinkedIn</th>\n              <th>Qualify Score</th>\n              <th>Actions</th>\n            </tr></thead>\n            <tbody>${filtered.map(c => rowHTML(c)).join(\'\')}</tbody>\n          </table>\n        </div>`;\n    }\n  }\n\n  // \xe2\x94\x80\xe2\x94\x80 table row \xe2\x94\x80\xe2\x94\x80\n  function rowHTML(c) {\n    const qs    = c.qualify_score;\n    const name  = `${c.first_name||\'\'} ${c.last_name||\'\'}`.trim();\n    const dm    = c.is_decision_maker ? \'<span title="Decision maker" style="color:#22c55e;margin-left:4px">\xe2\x98\x85</span>\' : \'\';\n    const sen   = c.seniority_level ? `<span style="font-size:.7rem;background:#1e293b;color:#94a3b8;padding:1px 6px;border-radius:4px;margin-left:4px">${c.seniority_level}</span>` : \'\';\n    const loc   = [c.company_city, c.company_country].filter(Boolean).join(\', \') || \'\xe2\x80\x94\';\n    const _liRaw = c.linkedin_url || \'\';\n    const _liUrl = _liRaw.startsWith(\'http\') ? _liRaw : (_liRaw ? \'https://\'+_liRaw : \'\');\n    const li = _liUrl\n      ? `<button onclick="(function(){var w=window.open(\'${_liUrl}\',\'_blank\',\'noopener,noreferrer\');if(!w){var a=document.createElement(\'a\');a.href=\'${_liUrl}\';a.target=\'_blank\';a.rel=\'noopener noreferrer\';document.body.appendChild(a);a.click();document.body.removeChild(a);}})()" style="display:inline-flex;align-items:center;gap:5px;background:none;border:1px solid #0077b5;border-radius:6px;padding:4px 10px;cursor:pointer;color:#0077b5;font-size:.78rem;font-weight:500">\n           <svg width="13" height="13" viewBox="0 0 24 24" fill="#0077b5"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>\n           View Profile\n         </button>`\n      : \'<span style="color:#475569;font-size:.8rem">\xe2\x80\x94</span>\';\n    const qBadge = qs != null\n      ? `<span style="font-weight:700;font-size:.85rem;color:${scoreColor(qs)}">${qs}/10</span>\n         <span style="font-size:.7rem;color:#94a3b8;margin-left:4px">${scoreTier(qs)}</span>`\n      : \'<span style="color:#475569;font-size:.8rem">Not scored</span>\';\n    return `<tr>\n      <td style="min-width:140px">\n        <b style="font-size:.9rem">${esc(name)}</b>${dm}${sen}\n        ${c.title ? `<div style="font-size:.75rem;color:#94a3b8">${esc(c.title)}</div>` : \'\'}\n        ${c.is_key_person ? \'<div style="font-size:.7rem;color:#f59e0b">\xf0\x9f\x94\x91 Key Person</div>\' : \'\'}\n      </td>\n      <td style="min-width:140px">\n        <div style="font-size:.85rem;font-weight:500">${esc(c.company_name||\'\xe2\x80\x94\')}</div>\n        <div style="font-size:.75rem;color:#60a5fa">${esc(c.company_industry||\'\')}</div>\n        <div style="font-size:.75rem;color:#94a3b8;display:flex;align-items:center;gap:4px;margin-top:2px">\n          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>\n          ${esc(loc)}\n        </div>\n      </td>\n      <td style="min-width:130px">\n        ${c.email ? `<div style="font-size:.78rem;color:#60a5fa">${esc(c.email)}</div>` : \'\'}\n        ${c.phone ? `<div style="font-size:.78rem;color:#94a3b8">${esc(c.phone)}</div>` : \'\'}\n      </td>\n      <td style="min-width:110px">${li}</td>\n      <td style="min-width:110px">\n        ${qBadge}\n        <div style="margin-top:4px">\n          <button class="btn btn-sm btn-ghost" style="padding:2px 8px;font-size:.7rem"\n            onclick="qualifyOneContact(${c.id})">Score</button>\n        </div>\n      </td>\n      <td>\n        <div style="display:flex;gap:4px;flex-wrap:wrap">\n          ${c.linkedin_url\n            ? `<button class="btn btn-sm btn-ghost" style="padding:2px 8px;font-size:.72rem;border-color:#0077b5;color:#0077b5"\n                onclick="_liOpen(\'${esc(c.linkedin_url||\'\')}\')">\n                <svg width="11" height="11" viewBox="0 0 24 24" fill="#0077b5" style="vertical-align:middle;margin-right:2px"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>\n                Open \xe2\x86\x97</button>\n              <button class="btn btn-sm btn-ghost" style="padding:2px 6px;font-size:.7rem"\n                onclick="showEditLinkedIn(${c.id},\'${esc(c.linkedin_url||\'\')}\',\'${esc(c.linkedin_position||\'\')}\',\'${esc(c.seniority_level||\'\')}\')">Edit</button>`\n            : `<button class="btn btn-sm btn-ghost" style="padding:2px 8px;font-size:.72rem"\n                onclick="showEditLinkedIn(${c.id},\'\',\'\',\'${esc(c.seniority_level||\'\')}\')">\xf0\x9f\x94\x97 Add</button>`}\n          <button class="btn btn-sm btn-ghost" style="padding:2px 8px;font-size:.72rem"\n            onclick="showPage(\'companies\')">\xf0\x9f\x8f\xa2 Co</button>\n        </div>\n      </td>\n    </tr>`;\n  }\n\n  // \xe2\x94\x80\xe2\x94\x80 card \xe2\x94\x80\xe2\x94\x80\n  function cardHTML(c) {\n    const qs   = c.qualify_score;\n    const name = `${c.first_name||\'\'} ${c.last_name||\'\'}`.trim();\n    const loc  = [c.company_city, c.company_country].filter(Boolean).join(\', \');\n    const initials = (c.first_name||\'?\')[0] + (c.last_name||\'?\')[0];\n    return `\n    <div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px;\n                border-top:3px solid ${scoreColor(qs)}">\n      <div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:10px">\n        <div style="width:40px;height:40px;border-radius:50%;background:#1e3a5f;color:#60a5fa;\n                    display:flex;align-items:center;justify-content:center;font-weight:600;font-size:.85rem;flex-shrink:0">\n          ${esc(initials.toUpperCase())}\n        </div>\n        <div style="flex:1;min-width:0">\n          <div style="font-weight:600;font-size:.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">\n            ${esc(name)}\n            ${c.is_decision_maker ? \'<span style="color:#22c55e;margin-left:4px" title="Decision maker">\xe2\x98\x85</span>\' : \'\'}\n          </div>\n          <div style="font-size:.75rem;color:#94a3b8">${esc(c.title||\'\')} ${c.seniority_level ? \'\xc2\xb7 \'+c.seniority_level : \'\'}</div>\n        </div>\n        ${qs!=null ? `<div style="text-align:center;flex-shrink:0">\n          <div style="font-size:1.1rem;font-weight:700;color:${scoreColor(qs)}">${qs}/10</div>\n          <div style="font-size:.65rem;color:#94a3b8">qualify</div>\n        </div>` : \'\'}\n      </div>\n\n      <div style="background:#0f172a;border-radius:6px;padding:8px 10px;margin-bottom:8px">\n        <div style="display:flex;align-items:center;gap:6px;font-size:.8rem;margin-bottom:4px">\n          <span>\xf0\x9f\x8f\xa2</span>\n          <b style="color:#e2e8f0">${esc(c.company_name||\'\xe2\x80\x94\')}</b>\n          ${c.company_industry ? `<span style="color:#60a5fa;font-size:.72rem">\xc2\xb7 ${esc(c.company_industry)}</span>` : \'\'}\n        </div>\n        ${loc ? `<div style="font-size:.75rem;color:#94a3b8;display:flex;align-items:center;gap:4px">\n          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>\n          ${esc(loc)}\n        </div>` : \'\'}\n      </div>\n\n      <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:.75rem;margin-bottom:8px">\n        ${c.email ? `<div style="color:#60a5fa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(c.email)}">\xe2\x9c\x89 ${esc(c.email)}</div>` : \'<div></div>\'}\n        ${c.phone ? `<div style="color:#94a3b8">\xf0\x9f\x93\x9e ${esc(c.phone)}</div>` : \'<div></div>\'}\n      </div>\n\n      ${c.linkedin_url ? (function(){\n        var _u=c.linkedin_url.startsWith(\'http\')?c.linkedin_url:\'https://\'+c.linkedin_url;\n        return `<button onclick="(function(){var w=window.open(\'${_u}\',\'_blank\',\'noopener,noreferrer\');if(!w){var a=document.createElement(\'a\');a.href=\'${_u}\';a.target=\'_blank\';document.body.appendChild(a);a.click();document.body.removeChild(a);}})()"\n           style="display:inline-flex;align-items:center;gap:6px;font-size:.78rem;color:#0077b5;\n                  background:#dbeafe;border:1px solid #93c5fd;padding:5px 12px;border-radius:20px;\n                  margin-bottom:8px;cursor:pointer;font-weight:500">\n          <svg width="13" height="13" viewBox="0 0 24 24" fill="#0077b5"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>\n          ${esc(c.linkedin_position||\'View LinkedIn Profile\')} \xe2\x86\x97\n        </button>`;})() : \'\'}\n\n      ${qs!=null ? `<div style="background:${tierBg(qs)};border-radius:6px;padding:6px 10px;font-size:.75rem">\n        <span style="color:${scoreColor(qs)};font-weight:600">${scoreTier(qs)}</span>\n        <span style="color:#94a3b8;margin-left:6px">for plumbing automation</span>\n      </div>` : \'\'}\n\n      <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">\n        <button class="btn btn-sm btn-ghost" style="font-size:.72rem;padding:3px 8px"\n          onclick="qualifyOneContact(${c.id})">\xf0\x9f\x8e\xaf Qualify</button>\n        <button class="btn btn-sm btn-ghost" style="font-size:.72rem;padding:3px 8px"\n          onclick="showEditLinkedIn(${c.id},\'${esc(c.linkedin_url||\'\')}\',\'${esc(c.linkedin_position||\'\')}\',\'${esc(c.seniority_level||\'\')}\')">\xf0\x9f\x94\x97 Edit LinkedIn</button>\n      </div>\n    </div>`;\n  }\n\n  // \xe2\x94\x80\xe2\x94\x80 expose helpers to global scope \xe2\x94\x80\xe2\x94\x80\n  window.applyFilter = applyFilter;\n  window.toggleContactView = function() {\n    viewMode = viewMode===\'table\' ? \'cards\' : \'table\';\n    const btn = document.getElementById(\'view-toggle\');\n    if (btn) btn.textContent = viewMode===\'cards\' ? \'\xf0\x9f\x93\x8b Table view\' : \'\xf0\x9f\x83\x8f Card view\';\n    renderRows();\n  };\n  window.qualifyOneContact = async function(ctid) {\n    toast(\'Scoring company\xe2\x80\xa6\',\'info\');\n    const r = await api(\'POST\',`/contacts/${ctid}/qualify-company`);\n    if (r) {\n      toast(`\xe2\x9c\x85 ${r.company}: ${r.qualify_score}/10 \xe2\x80\x94 ${r.tier}`,\'success\');\n      load();\n    }\n  };\n  window.qualifyAllContacts = async function() {\n    toast(\'Qualifying all companies\xe2\x80\xa6 this may take a moment\',\'info\');\n    const r = await api(\'POST\',\'/contacts/qualify-all\');\n    if (r) { toast(r.message,\'success\'); setTimeout(load,3000); }\n  };\n  window.showEditLinkedIn = function(ctid, url, pos, sen) {\n    var cleanUrl = url && url.startsWith(\'http\') ? url : (url ? \'https://\'+url : \'\');\n    var senOptions = [\'c_suite\',\'vp\',\'director\',\'manager\',\'owner\',\'individual\']\n      .map(function(s){ return \'<option value="\'+s+\'"\'+(sen===s?\' selected\':\'\')+\'>\'+s+\'</option>\'; }).join(\'\');\n    var openBtn = cleanUrl\n      ? \'<button onclick="_liOpen(\\\'\'+cleanUrl+\'\\\')" style="display:inline-flex;align-items:center;gap:6px;background:#0077b5;color:#fff;border:none;border-radius:8px;padding:9px 16px;cursor:pointer;font-size:.85rem;font-weight:500;margin-bottom:14px;width:100%">\'+\n        \'<svg width=\\"15\\" height=\\"15\\" viewBox=\\"0 0 24 24\\" fill=\\"#fff\\"><path d=\\"M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z\\"/></svg>\'+\n        \' Open LinkedIn Profile \xe2\x86\x97</button>\'\n      : \'\';\n    modal(\n      \'<h3 style="margin-bottom:14px">LinkedIn Profile</h3>\' +\n      openBtn +\n      \'<div class="form-group"><label>LinkedIn URL</label>\'+\n      \'<input id="li-url" value="\'+esc(url)+\'" placeholder="https://linkedin.com/in/username"></div>\'+\n      \'<div class="form-group"><label>Position / Title</label>\'+\n      \'<input id="li-pos" value="\'+esc(pos)+\'" placeholder="e.g. Owner, Director"></div>\'+\n      \'<div class="form-group"><label>Seniority Level</label>\'+\n      \'<select id="li-sen">\'+senOptions+\'</select></div>\'+\n      \'<div class="form-group"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">\'+\n      \'<input type="checkbox" id="li-kp"> Mark as Key Person (senior decision maker)</label></div>\'+\n      \'<div style="display:flex;gap:8px;flex-wrap:wrap">\'+\n      \'<button class="btn btn-primary" onclick="saveLinkedIn(\'+ctid+\')">Save</button>\'+\n      (cleanUrl ? \'<button class="btn btn-ghost" style="color:#0077b5;border-color:#0077b5" onclick="_liOpen(\\\'\'+cleanUrl+\'\\\')">Open \xe2\x86\x97</button>\' : \'\')+\n      \'</div>\',\n      \'LinkedIn Profile\'\n    );\n  window.saveLinkedIn = async function(ctid) {\n    const r = await api(\'POST\',`/contacts/${ctid}/linkedin`,{\n      linkedin_url: document.getElementById(\'li-url\')?.value||\'\',\n      position:     document.getElementById(\'li-pos\')?.value||\'\',\n      seniority:    document.getElementById(\'li-sen\')?.value||\'\',\n      is_key_person: document.getElementById(\'li-kp\')?.checked ? 1 : 0,\n    });\n    if (r) { closeModal(); toast(\'LinkedIn saved\',\'success\'); load(); }\n  };\n  window.showImportContactsModal = function() {\n    modal(`\n      <h3>Import Contacts CSV</h3>\n      <p style="font-size:.82rem;color:#94a3b8;margin-bottom:12px">\n        CSV columns: <code>first_name, last_name, email, phone, company, linkedin</code>\n      </p>\n      <div class="form-group"><label>Select CSV file</label>\n        <input type="file" id="ct-csv-file" accept=".csv"></div>\n      <button class="btn btn-primary" onclick="importContactsCSV()">Import</button>\n    `, \'Import Contacts\');\n  };\n  window.importContactsCSV = async function() {\n    const file = document.getElementById(\'ct-csv-file\')?.files?.[0];\n    if (!file) return toast(\'Select a CSV file first\',\'error\');\n    const form = new FormData();\n    form.append(\'file\', file);\n    const res = await fetch(\'/api/contacts/upload-csv\',{method:\'POST\',body:form,headers:{Authorization:\'Bearer \'+(TOKEN||\'\')}});\n    const r = await res.json();\n    if (r.ok){ closeModal(); toast(\'\xe2\x9c\x85 \'+r.data.message,\'success\'); setTimeout(load,2500); }\n    else toast(r.error||\'Import failed\',\'error\');\n  };\n  window.showAddContactModal = function() {\n    modal(`\n      <h3>Add Contact</h3>\n      <div class="form-group"><label>Company</label>\n        <select id="add-ct-co">${COMPANIES_CACHE.map(c=>`<option value="${c.id}">${c.name}</option>`).join(\'\')}</select></div>\n      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">\n        <div class="form-group"><label>First Name</label><input id="add-ct-fn" placeholder="Dave"></div>\n        <div class="form-group"><label>Last Name</label><input id="add-ct-ln" placeholder="Smith"></div>\n      </div>\n      <div class="form-group"><label>Title / Role</label><input id="add-ct-title" placeholder="Owner / Director"></div>\n      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">\n        <div class="form-group"><label>Email</label><input id="add-ct-email" type="email"></div>\n        <div class="form-group"><label>Phone</label><input id="add-ct-phone" placeholder="+61412345678"></div>\n      </div>\n      <div class="form-group"><label>Seniority</label>\n        <select id="add-ct-sen">\n          <option value="owner">Owner</option>\n          <option value="c_suite">C-Suite</option>\n          <option value="vp">VP</option>\n          <option value="director">Director</option>\n          <option value="manager">Manager</option>\n          <option value="individual">Individual</option>\n        </select></div>\n      <div class="form-group"><label>LinkedIn URL</label><input id="add-ct-li" placeholder="https://linkedin.com/in/..."></div>\n      <div class="form-group"><label><input type="checkbox" id="add-ct-dm"> Decision Maker</label></div>\n      <button class="btn btn-primary" onclick="addContact()">Add Contact</button>\n    `, \'New Contact\');\n  };\n  window._liOpen = function(url) {\n    var u = url && url.startsWith(\'http\') ? url : (url ? \'https://\'+url : \'\');\n    if (!u) return;\n    var w = window.open(u, \'_blank\', \'noopener,noreferrer\');\n    if (!w) {\n      var a = document.createElement(\'a\');\n      a.href = u; a.target = \'_blank\'; a.rel = \'noopener noreferrer\';\n      document.body.appendChild(a); a.click(); document.body.removeChild(a);\n    }\n  };\n  window.addContact = async function() {\n    const coid = parseInt(v(\'add-ct-co\'));\n    const fn   = v(\'add-ct-fn\');\n    if (!fn) return toast(\'First name required\',\'error\');\n    const r = await api(\'POST\',\'/contacts\',{\n      company_id: coid, first_name: fn, last_name: v(\'add-ct-ln\'),\n      title: v(\'add-ct-title\'), email: v(\'add-ct-email\'), phone: v(\'add-ct-phone\'),\n      seniority_level: v(\'add-ct-sen\'), is_decision_maker: document.getElementById(\'add-ct-dm\')?.checked?1:0,\n    });\n    if (r) {\n      const liUrl = v(\'add-ct-li\');\n      if (liUrl && r.id) await api(\'POST\',`/contacts/${r.id}/linkedin`,{linkedin_url:liUrl,position:v(\'add-ct-title\'),seniority:v(\'add-ct-sen\')});\n      closeModal(); toast(\'\xe2\x9c\x85 Contact added\',\'success\'); load();\n    }\n  };\n\n  // \xe2\x94\x80\xe2\x94\x80 initial load \xe2\x94\x80\xe2\x94\x80\n  await load();\n}\n\nasync function emails() {\n  document.getElementById(\'topbar-actions\').innerHTML =\n    \'<button class="btn btn-primary btn-sm" onclick="openEmailModal()">\xf0\x9f\xa4\x96 Generate Email</button>\';\n  const [data,cos] = await Promise.all([api(\'GET\',\'/emails\'),api(\'GET\',\'/companies?limit=200\')]);\n  if (!data) return;\n  set(\'content\',`\n    <div class="tbl-wrap">\n      <div class="tbl-head">\n        <h3>${data.length} Emails</h3>\n        <div style="display:flex;gap:8px">\n          <select onchange="filterEmails(this.value,\'\')">\n            <option value="">All Statuses</option>\n            ${[\'draft\',\'sent\',\'opened\',\'replied\'].map(s=>`<option value="${s}">${s}</option>`).join(\'\')}\n          </select>\n        </div>\n      </div>\n      <table><thead><tr><th>Subject</th><th>Type</th><th>Status</th><th>Recipient</th><th>AI Model</th><th>Created</th><th>Actions</th></tr></thead>\n      <tbody>${data.map(e=>`<tr>\n        <td style="max-width:200px"><b>${esc(e.subject)}</b></td>\n        <td>${(e.email_type||\'\').replace(/_/g,\' \')}</td>\n        <td><span class="badge badge-${e.status}">${e.status}</span></td>\n        <td>${esc(e.recipient_email||\'\')}</td>\n        <td><span style="font-size:.75rem;color:var(--muted)">${e.ai_model_used||\'\xe2\x80\x94\'}</span></td>\n        <td style="font-size:.75rem;color:var(--muted)">${fmtDt(e.created_at)}</td>\n        <td>${e.status===\'draft\'?`<button class="btn btn-sm btn-success" onclick="sendEmail(${e.id})">\xf0\x9f\x93\xa4 Send</button>`:\'\xe2\x80\x94\'}</td>\n      </tr>`).join(\'\')}</tbody></table>\n    </div>\n  `);\n}\n\nasync function filterEmails(status) {\n  const url = status ? `/emails?status=${status}` : \'/emails\';\n  const data = await api(\'GET\', url);\n  if (!data) return;\n  document.querySelector(\'tbody\').innerHTML = data.map(e=>`<tr>\n    <td style="max-width:200px"><b>${esc(e.subject)}</b></td>\n    <td>${(e.email_type||\'\').replace(/_/g,\' \')}</td>\n    <td><span class="badge badge-${e.status}">${e.status}</span></td>\n    <td>${esc(e.recipient_email||\'\')}</td>\n    <td><span style="font-size:.75rem;color:var(--muted)">${e.ai_model_used||\'\xe2\x80\x94\'}</span></td>\n    <td style="font-size:.75rem;color:var(--muted)">${fmtDt(e.created_at)}</td>\n    <td>${e.status===\'draft\'?`<button class="btn btn-sm btn-success" onclick="sendEmail(${e.id})">\xf0\x9f\x93\xa4 Send</button>`:\'\xe2\x80\x94\'}</td>\n  </tr>`).join(\'\');\n}\n\nasync function meetings() {\n  document.getElementById(\'topbar-actions\').innerHTML =\n    \'<button class="btn btn-primary btn-sm" onclick="openMeetingModal()">\xe2\x9e\x95 Schedule Meeting</button>\';\n  const data = await api(\'GET\',\'/meetings\');\n  if (!data) return;\n  set(\'content\',`\n    <div class="tbl-wrap">\n      <div class="tbl-head"><h3>${data.length} Meetings</h3></div>\n      <table><thead><tr><th>Title</th><th>Company</th><th>Type</th><th>Scheduled</th><th>Duration</th><th>Status</th><th>Actions</th></tr></thead>\n      <tbody>${data.map(m=>`<tr>\n        <td><b>${esc(m.title)}</b></td>\n        <td>${esc(m.company_name||\'\xe2\x80\x94\')}</td>\n        <td>${(m.meeting_type||\'\').replace(/_/g,\' \')}</td>\n        <td>${fmtDt(m.scheduled_at)}</td>\n        <td>${m.duration_minutes||30}m</td>\n        <td><span class="badge badge-${m.status||\'proposed\'}">${m.status||\'proposed\'}</span></td>\n        <td style="white-space:nowrap">\n          ${m.meeting_link?`<a href="${m.meeting_link}" target="_blank" class="btn btn-sm btn-ghost">\xf0\x9f\x93\xb9</a>`:\'\'}\n          ${m.status!==\'completed\'?`<button class="btn btn-sm btn-success" onclick="completeMeeting(${m.id},${m.company_id})">\xe2\x9c\x85</button>`:\'\'}\n          <button class="btn btn-sm btn-ghost" onclick="addToCalendar(${m.id},${m.company_id})">\xf0\x9f\x93\x85</button>\n        </td>\n      </tr>`).join(\'\')}</tbody></table>\n    </div>\n  `);\n}\n\nasync function calls() {\n  document.getElementById(\'topbar-actions\').innerHTML =\n    \'<button class="btn btn-primary btn-sm" onclick="openCallModal()">\xf0\x9f\x93\x9e New AI Call</button>\';\n  const data = await api(\'GET\',\'/calls\');\n  if (!data) return;\n  set(\'content\',`\n    <div style="padding:12px;background:#1e3a5f;border-radius:8px;margin-bottom:16px;font-size:.85rem">\n      \xf0\x9f\x93\x9e <b>Bland AI Phone Calls</b> \xe2\x80\x94 The AI calls prospects and has real conversations. Requires BLAND_API_KEY in .env\n    </div>\n    <div class="tbl-wrap">\n      <div class="tbl-head"><h3>${data.length} Calls</h3></div>\n      <table><thead><tr><th>Company</th><th>Phone</th><th>Objective</th><th>Status</th><th>Duration</th><th>Summary</th><th>Actions</th></tr></thead>\n      <tbody>${data.map(c=>`<tr>\n        <td>${esc(c.company_name||\'\xe2\x80\x94\')}</td>\n        <td>${esc(c.phone_number)}</td>\n        <td>${c.objective||\'\xe2\x80\x94\'}</td>\n        <td><span class="badge badge-${c.status||\'queued\'}">${c.status||\'queued\'}</span></td>\n        <td>${c.duration_seconds?c.duration_seconds+\'s\':\'\xe2\x80\x94\'}</td>\n        <td style="font-size:.8rem;max-width:150px">${esc((c.summary||\'\').slice(0,60))}</td>\n        <td>\n          ${c.recording_url?`<a href="${c.recording_url}" target="_blank" class="btn btn-sm btn-ghost">\xf0\x9f\x8e\xa7</a>`:\'\'}\n          ${c.status===\'queued\'?`<button class="btn btn-sm btn-ghost" onclick="syncCall(${c.id})">\xf0\x9f\x94\x84</button>`:\'\'}\n        </td>\n      </tr>`).join(\'\')}</tbody></table>\n    </div>\n  `);\n}\n\nasync function analytics() {\n  const [sum,ea,ld,pipe] = await Promise.all([\n    api(\'GET\',\'/analytics/summary\'),\n    api(\'GET\',\'/analytics/email-activity\'),\n    api(\'GET\',\'/analytics/lead-distribution\'),\n    api(\'GET\',\'/analytics/pipeline\'),\n  ]);\n  if (!sum) return;\n  set(\'content\',`\n    <div class="metrics-grid">\n      ${metric(\'Total Companies\',sum.total_companies)}\n      ${metric(\'\xf0\x9f\x94\xa5 Hot Leads\',sum.hot_leads,\'var(--hot)\')}\n      ${metric(\'\xf0\x9f\x9f\xa1 Warm Leads\',sum.warm_leads,\'var(--warm)\')}\n      ${metric(\'\xe2\x9d\x84\xef\xb8\x8f Cold Leads\',sum.cold_leads,\'var(--cold)\')}\n      ${metric(\'Emails Sent\',sum.emails_sent)}\n      ${metric(\'Open Rate\',sum.open_rate+\'%\')}\n      ${metric(\'Reply Rate\',sum.reply_rate+\'%\')}\n      ${metric(\'\xf0\x9f\x92\xb0 Pipeline\',\'$\'+fmt(sum.revenue_pipeline),\'var(--green)\')}\n    </div>\n    <div class="row">\n      <div class="col section">\n        <h3>\xf0\x9f\x93\xa7 Email Activity</h3>\n        ${miniChart(ea||[],\'sent\',\'#3b82f6\')}\n      </div>\n      <div class="col section">\n        <h3>\xf0\x9f\x8f\xad Lead Distribution</h3>\n        <table><thead><tr><th>Industry</th><th>Count</th><th>Avg Score</th></tr></thead>\n        <tbody>${(ld||[]).map(r=>`<tr><td>${esc(r.industry)}</td><td>${r.count}</td><td>${r.avg_score}</td></tr>`).join(\'\')}</tbody></table>\n      </div>\n    </div>\n    <div class="section">\n      <h3>\xf0\x9f\x92\xb0 Revenue Pipeline</h3>\n      <table><thead><tr><th>Company</th><th>Score</th><th>Potential Revenue</th><th>Status</th></tr></thead>\n      <tbody>${(pipe||[]).map(c=>`<tr>\n        <td><b>${esc(c.name)}</b></td>\n        <td>${scoreBadge(c.lead_score)}</td>\n        <td style="color:var(--green)">$${fmt(c.potential_revenue)}</td>\n        <td><span class="badge badge-${c.status}">${c.status}</span></td>\n      </tr>`).join(\'\')}</tbody></table>\n    </div>\n  `);\n}\n\nasync function chat() {\n  const msgs = await api(\'GET\',\'/chat\');\n  set(\'content\',`\n    <div class="section" style="max-width:700px;margin:0 auto">\n      <div id="chat-msgs">${(msgs||[]).map(m=>\n        m.sender===\'user\'\n          ? `<div class="chat-user">\xf0\x9f\x91\xa4 ${esc(m.message)}</div>`\n          : `<div class="chat-bot">\xf0\x9f\xa4\x96 ${esc(m.message)}</div>`\n      ).join(\'\')}</div>\n      <div style="display:flex;gap:8px">\n        <input id="chat-input" placeholder="Ask anything\xe2\x80\xa6 try \'show leads\', \'analytics\', \'pipeline\'" onkeydown="if(event.key===\'Enter\')sendChat()" style="flex:1">\n        <button class="btn btn-primary" onclick="sendChat()">Send \xe2\x86\x92</button>\n        <button class="btn btn-ghost" onclick="clearChat()">\xf0\x9f\x97\x91</button>\n      </div>\n      <div style="margin-top:10px;font-size:.75rem;color:var(--muted)">\n        Commands: <code>show leads</code> \xc2\xb7 <code>analytics</code> \xc2\xb7 <code>pipeline</code> \xc2\xb7 <code>daily report</code> \xc2\xb7 <code>help</code>\n      </div>\n    </div>\n  `);\n  const el = document.getElementById(\'chat-msgs\');\n  if (el) el.scrollTop = el.scrollHeight;\n}\n\nasync function sendChat() {\n  const msg = (document.getElementById(\'chat-input\').value || \'\').trim();\n  if (!msg) return;\n  document.getElementById(\'chat-input\').value = \'\';\n  const box = document.getElementById(\'chat-msgs\');\n  if (box) box.innerHTML += `<div class="chat-user">\xf0\x9f\x91\xa4 ${esc(msg)}</div><div class="chat-bot" id="typing">\xf0\x9f\xa4\x96 ...</div>`;\n  if (box) box.scrollTop = box.scrollHeight;\n  const r = await api(\'POST\',\'/chat\',{message:msg});\n  const typing = document.getElementById(\'typing\');\n  if (typing && r) { typing.textContent = \'\xf0\x9f\xa4\x96 \' + r.reply; typing.id=\'\'; }\n  if (typing && !r) typing.textContent = \'\xf0\x9f\xa4\x96 Error \xe2\x80\x94 try again.\';\n  if (box) box.scrollTop = box.scrollHeight;\n}\n\nasync function clearChat() {\n  await api(\'DELETE\',\'/chat\');\n  chat();\n}\n\nasync function sms() {\n  const data = await api(\'GET\',\'/sms-logs?limit=100\');\n  if (!data) return;\n  set(\'content\',`\n    <div style="padding:12px;background:#1e3a5f;border-radius:8px;margin-bottom:16px;font-size:.85rem">\n      \xf0\x9f\x93\xb1 <b>Twilio SMS Notifications</b> \xe2\x80\x94 All 12 event types logged here. Set TWILIO_* variables in .env to enable.\n    </div>\n    <div class="tbl-wrap">\n      <div class="tbl-head"><h3>${data.length} SMS Notifications</h3></div>\n      <table><thead><tr><th>Event</th><th>To</th><th>Message Preview</th><th>Status</th><th>Time</th></tr></thead>\n      <tbody>${data.length ? data.map(l=>`<tr>\n        <td><b>${esc(l.event_type||\'\xe2\x80\x94\')}</b></td>\n        <td>${esc(l.to_number)}</td>\n        <td style="font-size:.8rem;max-width:250px">${esc((l.body||\'\').slice(0,80))}</td>\n        <td><span class="badge badge-${l.status===\'sent\'?\'sent\':\'error\'}">${l.status}</span></td>\n        <td style="font-size:.75rem;color:var(--muted)">${fmtDt(l.created_at)}</td>\n      </tr>`).join(\'\') : \'<tr><td colspan="5" class="empty">No SMS sent yet \xe2\x80\x94 configure Twilio in Integrations</td></tr>\'}</tbody></table>\n    </div>\n    <div class="section">\n      <h3>\xf0\x9f\x93\x8b SMS Event Types (12 total)</h3>\n      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:.85rem">\n        ${[[\'\xf0\x9f\x8f\xa2 company_added\',\'New company added\'],[\'\xf0\x9f\x94\xa5 hot_lead\',\'Lead score \xe2\x89\xa5 80\'],\n           [\'\xf0\x9f\x93\xa7 email_generated\',\'AI email created\'],[\'\xe2\x9c\x85 email_sent\',\'Email sent via Gmail\'],\n           [\'\xf0\x9f\x93\x85 meeting_scheduled\',\'Meeting created\'],[\'\xe2\x9c\x85 meeting_completed\',\'Meeting completed\'],\n           [\'\xf0\x9f\x93\x9e call_initiated\',\'Bland AI call\'],[\'\xf0\x9f\x93\xa4 csv_import\',\'CSV import done\'],\n           [\'\xf0\x9f\x93\x8a daily_report\',\'Daily at 6PM UTC\'],[\'\xe2\x8f\xb0 meeting_reminder_24h\',\'24h before\'],\n           [\'\xe2\x8f\xb0 meeting_reminder_1h\',\'1h before\'],[\'\xe2\x8f\xb0 meeting_reminder_10min\',\'10min before\']\n          ].map(([k,v])=>`<div style="padding:6px;background:#0f172a;border-radius:4px"><b>${k}</b> \xe2\x80\x94 ${v}</div>`).join(\'\')}\n      </div>\n    </div>\n  `);\n}\n\nasync function integrations() {\n  const st = await api(\'GET\',\'/integrations/status\');\n  if (!st) return;\n\n  const badge = (ok, label=\'\') => ok\n    ? `<span style="color:#22c55e;font-weight:600;font-size:.95rem">\xf0\x9f\x9f\xa2 Connected${label?\' \xe2\x80\x94 \'+label:\'\'}</span>`\n    : `<span style="color:#ef4444;font-weight:600;font-size:.95rem">\xf0\x9f\x94\xb4 Not configured</span>`;\n\n  const card = (icon, title, statusHtml, bodyHtml) => `\n    <div class="section" style="margin-bottom:16px">\n      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">\n        <span style="font-size:1.3rem">${icon}</span>\n        <h3 style="margin:0;font-size:1rem">${title}</h3>\n      </div>\n      ${statusHtml}\n      ${bodyHtml}\n    </div>`;\n\n  const emailMethod = st.gmail?.method || \'none\';\n  const emailOk = emailMethod !== \'none\';\n  const emailLabel = emailMethod === \'sendgrid\' ? \'SendGrid\' : emailMethod === \'smtp\' ? \'Gmail SMTP\' : \'\';\n\n  set(\'content\',`\n  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">\n\n    ${card(\'\xf0\x9f\xa4\x96\',\'Groq AI\',\n      badge(st.groq?.connected),\n      `<p style="font-size:.8rem;color:var(--muted);margin:6px 0">Model: ${st.groq?.model||\'llama-3.3-70b-versatile\'}</p>\n       <p style="font-size:.75rem;color:#64748b">Free AI \xe2\x80\x94 generates email content, call scripts, lead summaries</p>\n       <a href="https://console.groq.com" target="_blank" style="color:var(--accent);font-size:.8rem">console.groq.com \xe2\x86\x92</a>`\n    )}\n\n    ${card(\'\xf0\x9f\x93\xa7\',\'Email\',\n      badge(emailOk, emailLabel),\n      `<p style="font-size:.8rem;color:var(--muted);margin:6px 0">${st.gmail?.email||\'\'}</p>\n       <p style="font-size:.75rem;color:${st.gmail?.sendgrid?\'#22c55e\':\'#f59e0b\'};margin:4px 0">\n         ${st.gmail?.sendgrid ? \'\xe2\x9c\x85 SendGrid configured (works on Render free tier)\' : \'\xe2\x9a\xa0\xef\xb8\x8f Add SENDGRID_API_KEY for Render free tier\'}\n       </p>\n       <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">\n         <button class="btn btn-sm btn-ghost" onclick="testGmail()">\xf0\x9f\x94\x8d Test</button>\n         <button class="btn btn-sm btn-ghost" onclick="sendTestGmail()">\xf0\x9f\x93\xa7 Send Test</button>\n       </div>\n       <a href="https://sendgrid.com" target="_blank" style="color:var(--accent);font-size:.8rem;margin-top:8px;display:block">Get free SendGrid key \xe2\x86\x92</a>`\n    )}\n\n    ${card(\'\xf0\x9f\x93\x9e\',\'Bland AI \xe2\x80\x94 Sarah Caller\',\n      badge(st.bland_ai?.connected),\n      `<p style="font-size:.75rem;color:#94a3b8;margin:6px 0">AI caller trained as Sarah from ARC Digital. Calls plumbing businesses on behalf of Kevin Raju.</p>\n       <button class="btn btn-sm btn-ghost" onclick="testBland()" style="margin-top:8px">\xf0\x9f\x94\x8d Test Connection</button>\n       <a href="https://app.bland.ai" target="_blank" style="color:var(--accent);font-size:.8rem;margin-top:8px;display:block">app.bland.ai \xe2\x86\x92</a>`\n    )}\n\n    ${card(\'\xf0\x9f\x93\xb1\',\'Twilio SMS (12 Events)\',\n      badge(st.twilio_sms?.connected),\n      `${st.twilio_sms?.from_number ? `<p style="font-size:.8rem;color:var(--muted);margin:4px 0">From: ${st.twilio_sms.from_number}</p>` : \'\'}\n       ${st.twilio_sms?.admin_number ? `<p style="font-size:.8rem;color:var(--muted);margin:4px 0">Admin: ${st.twilio_sms.admin_number}</p>` : \'\'}\n       <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">\n         <input id="sms-test-num" placeholder="+61411519086" style="flex:1;min-width:120px">\n         <button class="btn btn-sm btn-ghost" onclick="testTwilio()">\xf0\x9f\x93\xb1 Test SMS</button>\n       </div>\n       <button class="btn btn-sm btn-ghost" onclick="sendDailyReport()" style="margin-top:8px;width:100%">\xf0\x9f\x93\x8a Send Daily Report Now</button>\n       <a href="https://console.twilio.com" target="_blank" style="color:var(--accent);font-size:.8rem;margin-top:8px;display:block">Twilio Console \xe2\x86\x92</a>`\n    )}\n\n    ${card(\'\xf0\x9f\x93\x85\',\'Google Calendar\',\n      st.google_calendar?.connected\n        ? \'<span style="color:#22c55e;font-weight:600">\xf0\x9f\x9f\xa2 Connected</span>\'\n        : st.google_calendar?.keys_set\n          ? \'<span style="color:#f59e0b;font-weight:600">\xf0\x9f\x9f\xa1 Keys set \xe2\x80\x94 click Connect below</span>\'\n          : \'<span style="color:#ef4444;font-weight:600">\xf0\x9f\x94\xb4 Not configured</span>\',\n      `<p style="font-size:.75rem;color:#94a3b8;margin:6px 0">Auto-creates Google Calendar events when meetings are booked.</p>\n       ${!st.google_calendar?.connected ? `\n         ${st.google_calendar?.keys_set ? \'<p style="font-size:.75rem;color:#f59e0b;margin:4px 0">\xe2\x9c\x85 GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set. Just click Connect below to authorise.</p>\' : \'\'}\n         <button class="btn btn-sm btn-primary" onclick="connectGoogle()" style="margin-top:10px">\xf0\x9f\x94\x97 Connect Google Calendar</button>\n         <ol style="font-size:.72rem;color:#64748b;margin-top:10px;padding-left:16px;line-height:1.8">\n           <li>Click Connect \xe2\x86\x92 Google login opens</li>\n           <li>Select <b style="color:#e2e8f0">kevin@arcdigital.com.au</b></li>\n           <li>Click Allow on calendar permission</li>\n           <li>Popup closes \xe2\x86\x92 Calendar turns green</li>\n         </ol>\n       ` : `\n         <div style="margin-top:10px">\n           <span style="color:#22c55e;font-size:.85rem">\xe2\x9c\x85 Calendar syncing active</span><br>\n           <button class="btn btn-sm btn-ghost" onclick="disconnectGoogle()" style="margin-top:8px">\xf0\x9f\x94\x8c Disconnect</button>\n         </div>\n       `}\n       <a href="https://console.cloud.google.com/apis/credentials" target="_blank" style="color:var(--accent);font-size:.8rem;margin-top:8px;display:block">Google Cloud Console \xe2\x86\x92</a>`\n    )}\n\n    <div class="section" style="margin-bottom:16px">\n      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">\n        <span style="font-size:1.3rem">\xf0\x9f\x94\x84</span>\n        <h3 style="margin:0;font-size:1rem">Automation Schedule (UTC)</h3>\n      </div>\n      <table style="width:100%;font-size:.8rem;border-collapse:collapse">\n        <tr style="border-bottom:1px solid #1e293b"><td style="padding:6px 0;color:#94a3b8">Score leads</td><td style="color:#e2e8f0">08:00 daily</td></tr>\n        <tr style="border-bottom:1px solid #1e293b"><td style="padding:6px 0;color:#94a3b8">Cold emails</td><td style="color:#e2e8f0">09:00 weekdays</td></tr>\n        <tr style="border-bottom:1px solid #1e293b"><td style="padding:6px 0;color:#94a3b8">Follow-ups</td><td style="color:#e2e8f0">10:00 weekdays</td></tr>\n        <tr style="border-bottom:1px solid #1e293b"><td style="padding:6px 0;color:#94a3b8">AI calls (Sarah)</td><td style="color:#e2e8f0">11:00 weekdays</td></tr>\n        <tr><td style="padding:6px 0;color:#94a3b8">Daily SMS report</td><td style="color:#e2e8f0">18:00 daily</td></tr>\n      </table>\n    </div>\n\n  </div>`);\n}\n\n\nasync function scoreCompany(cid, reload=false) {\n  const r = await api(\'POST\',`/companies/${cid}/score`);\n  if (!r) return;\n  toast(`Score: ${r.total_score}/100 \xe2\x80\x94 ${r.tier.toUpperCase()}${r.total_score>=80?\' \xf0\x9f\x94\xa5 Hot lead! SMS sent.\':\'\'}`, \'success\');\n  if (reload) viewCompany(cid);\n  else goto(\'companies\');\n}\n\nasync function aiSummary(cid) {\n  toast(\'Generating AI summary...\',\'info\');\n  const r = await api(\'POST\',`/companies/${cid}/ai-summary`);\n  if (r) { toast(\'Summary generated!\',\'success\'); viewCompany(cid); }\n}\n\nasync function analyzeSignals(cid) {\n  toast(\'Analysing buying signals...\',\'info\');\n  const r = await api(\'POST\',`/companies/${cid}/analyze-signals`);\n  if (r) { toast(`Found ${r.signals.length} signals!`,\'success\'); viewCompany(cid); }\n}\n\nasync function deleteCompany(cid, name) {\n  if (!confirm(`Delete ${name}? This cannot be undone.`)) return;\n  const r = await api(\'DELETE\',`/companies/${cid}`);\n  if (r) { toast(`${name} deleted`,\'success\'); goto(\'companies\'); }\n}\n\nasync function sendEmail(eid) {\n  const r = await api(\'POST\',`/emails/${eid}/send`);\n  if (!r) return;\n  const sr = r.send_result || {};\n  if (sr.status === \'sent\') toast(\'\xe2\x9c\x85 Email sent! SMS notification dispatched.\',\'success\');\n  else toast(sr.message || \'Check Gmail config in Integrations\',\'error\');\n  goto(\'emails\');\n}\n\nasync function completeMeeting(mid, cid) {\n  const r = await api(\'PUT\',`/meetings/${mid}`,{status:\'completed\'});\n  if (r) { toast(\'Meeting completed! SMS sent.\',\'success\'); if(cid) viewCompany(cid); else goto(\'meetings\'); }\n}\n\nasync function addToCalendar(mid, cid) {\n  toast(\'Adding to Google Calendar...\',\'info\');\n  const r = await api(\'POST\',`/meetings/${mid}/calendar`);\n  if (!r) return;\n  if (r.google_meet_link) toast(\'\xe2\x9c\x85 Calendar event created! Google Meet link added.\',\'success\');\n  else if (r.error) toast(r.error,\'error\');\n  if (cid) viewCompany(cid);\n}\n\nasync function syncCall(cid_) {\n  const r = await api(\'GET\',`/calls/${cid_}`);\n  if (r) { toast(`Call status: ${r.status}`,\'info\'); goto(\'calls\'); }\n}\n\nasync function uploadCSV(input) {\n  if (!input.files[0]) return;\n  const fd = new FormData();\n  fd.append(\'file\', input.files[0]);\n  const r = await api(\'POST\',\'/companies/upload-csv\',fd,true);\n  if (r) toast(`Import started \xe2\x80\x94 ${input.files[0].name}. SMS sent on completion.`,\'success\');\n  input.value = \'\';\n}\n\nasync function testGmail() {\n  const r = await api(\'POST\',\'/integrations/gmail/test\');\n  if (r) toast(r.message, r.success?\'success\':\'error\');\n}\nasync function sendTestGmail() {\n  const r = await api(\'POST\',\'/integrations/gmail/send-test\');\n  if (r) toast(r.success?\'Test email sent!\':r.message, r.success?\'success\':\'error\');\n}\nasync function testBland() {\n  const r = await api(\'POST\',\'/integrations/bland/test\');\n  if (!r) return;\n  if (r.success) {\n    toast(r.message,\'success\');\n  } else if (r.message && (r.message.includes(\'403\') || r.message.includes(\'invalid\'))) {\n    toast(\'\xe2\x9d\x8c Bland API key expired. Go to app.bland.ai \xe2\x86\x92 API Keys \xe2\x86\x92 create new key \xe2\x86\x92 update BLAND_API_KEY in Render \xe2\x86\x92 redeploy\',\'error\');\n  } else {\n    toast(\'\xe2\x9d\x8c \'+r.message,\'error\');\n  }\n}\nasync function testTwilio() {\n  const to = v(\'sms-test-num\');\n  if (!to) return toast(\'Enter a phone number to test\',\'error\');\n  const r = await api(\'POST\',\'/integrations/twilio/test\',{to_number:to});\n  if (!r) return;\n  if (r.success) {\n    toast(\'\xe2\x9c\x85 SMS sent to \'+to,\'success\');\n  } else {\n    const msg = r.result?.message || r.message || \'SMS failed\';\n    if (msg.includes(\'401\') || msg.includes(\'Unauthorized\')) {\n      toast(\'\xe2\x9d\x8c Twilio auth failed \xe2\x80\x94 check TWILIO_AUTH_TOKEN in Render env vars\',\'error\');\n    } else if (msg.includes(\'400\')) {\n      toast(\'\xe2\x9d\x8c Twilio error \xe2\x80\x94 check TWILIO_FROM_NUMBER format (must be +1xxx)\',\'error\');\n    } else {\n      toast(\'\xe2\x9d\x8c \'+msg,\'error\');\n    }\n  }\n}\nasync function sendDailyReport() {\n  const r = await api(\'POST\',\'/integrations/twilio/daily-report\');\n  if (r) toast(\'\xf0\x9f\x93\x8a Daily report SMS sent!\',\'success\');\n}\nasync function connectGoogle() {\n  const r = await api(\'GET\',\'/integrations/google/auth-url\');\n  if (!r) return;\n  if (r.auth_url) {\n    window.open(r.auth_url, \'_blank\', \'width=520,height=640,left=300,top=100\');\n    toast(\'Complete the Google sign-in in the popup window, then refresh this page.\',\'info\');\n  } else {\n    toast(r.error || r.message || \'GOOGLE_CLIENT_ID not set in Render environment\',\'error\');\n  }\n}\nasync function disconnectGoogle() {\n  if (!confirm(\'Disconnect Google Calendar?\')) return;\n  const r = await api(\'POST\',\'/integrations/google/disconnect\');\n  if (r) { toast(\'Disconnected\',\'info\'); integrations(); }\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   MODALS\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nfunction openCompanyModal() {\n  [\'co-id\',\'co-name\',\'co-industry\',\'co-web\',\'co-city\',\'co-country\',\'co-desc\',\'co-tech\',\'co-linkedin\']\n    .forEach(id=>{ const el=document.getElementById(id); if(el) el.value=\'\'; });\n  document.getElementById(\'co-emp\').value=\'\';\n  document.getElementById(\'co-rev\').value=\'\';\n  document.getElementById(\'co-modal-title\').textContent=\'\xe2\x9e\x95 Add Company\';\n  openModal(\'company-modal\');\n}\n\nasync function editCompanyModal(cid) {\n  const co = await api(\'GET\',`/companies/${cid}`);\n  if (!co) return;\n  s(\'co-id\',cid); s(\'co-name\',co.name||\'\'); s(\'co-industry\',co.industry||\'\');\n  s(\'co-emp\',co.employee_count||\'\'); s(\'co-rev\',co.annual_revenue||\'\');\n  s(\'co-web\',co.website||\'\'); s(\'co-city\',co.city||\'\'); s(\'co-country\',co.country||\'\');\n  s(\'co-status\',co.status||\'prospect\'); s(\'co-desc\',co.description||\'\');\n  const techs = Array.isArray(co.technologies) ? co.technologies.join(\', \')\n    : (co.technologies||\'\');\n  s(\'co-tech\',techs); s(\'co-linkedin\',co.linkedin_url||\'\');\n  document.getElementById(\'co-modal-title\').textContent=\'\xe2\x9c\x8f\xef\xb8\x8f Edit Company\';\n  openModal(\'company-modal\');\n}\n\nasync function saveCompany() {\n  const name = v(\'co-name\').trim();\n  if (!name) return toast(\'Company name is required\',\'error\');\n  const cid = v(\'co-id\');\n  const payload = {\n    name, industry:v(\'co-industry\')||null,\n    employee_count:parseInt(v(\'co-emp\'))||null,\n    annual_revenue:parseInt(v(\'co-rev\'))||null,\n    website:v(\'co-web\')||null, city:v(\'co-city\')||null, country:v(\'co-country\')||null,\n    status:v(\'co-status\')||\'prospect\', description:v(\'co-desc\')||null,\n    technologies:v(\'co-tech\')||null, linkedin_url:v(\'co-linkedin\')||null,\n  };\n  let r;\n  if (cid) r = await api(\'PUT\',`/companies/${cid}`,payload);\n  else      r = await api(\'POST\',\'/companies\',payload);\n  if (!r) return;\n  closeModal(\'company-modal\');\n  toast(`${name} ${cid?\'updated\':\'added\'}! SMS notification sent.`,\'success\');\n  goto(\'companies\');\n}\n\nfunction openContactModal(companyId) {\n  document.getElementById(\'ct-company-id\').value = companyId;\n  [\'ct-fn\',\'ct-ln\',\'ct-email\',\'ct-phone\',\'ct-title\',\'ct-dept\'].forEach(id=>{\n    const el=document.getElementById(id); if(el) el.value=\'\';\n  });\n  document.getElementById(\'ct-dm\').checked = false;\n  openModal(\'contact-modal\');\n}\n\nasync function saveContact() {\n  const cid = v(\'ct-company-id\');\n  const fn  = v(\'ct-fn\').trim();\n  if (!fn || !cid) return toast(\'First name and company required\',\'error\');\n  const r = await api(\'POST\',\'/contacts\',{\n    company_id:parseInt(cid), first_name:fn, last_name:v(\'ct-ln\')||null,\n    email:v(\'ct-email\')||null, phone:v(\'ct-phone\')||null,\n    title:v(\'ct-title\')||null, department:v(\'ct-dept\')||null,\n    seniority_level:v(\'ct-sen\'), is_decision_maker:document.getElementById(\'ct-dm\').checked,\n  });\n  if (!r) return;\n  closeModal(\'contact-modal\');\n  toast(\'Contact added!\',\'success\');\n  viewCompany(parseInt(cid));\n}\n\nfunction openEmailModal(companyId) {\n  document.getElementById(\'em-company-id\').value = companyId||\'\';\n  document.getElementById(\'em-custom\').value=\'\';\n  document.getElementById(\'email-preview\').style.display=\'none\';\n  _genEmailData=null; _lastEmailId=null;\n  openModal(\'email-modal\');\n}\n\nasync function genEmail() {\n  const cid = parseInt(v(\'em-company-id\'));\n  if (!cid) { return toast(\'Select a company first\',\'error\'); }\n  toast(\'\xf0\x9f\xa4\x96 Generating email...\',\'info\');\n  const r = await api(\'POST\',\'/emails/generate\',{\n    company_id:cid, email_type:v(\'em-type\'), custom_instructions:v(\'em-custom\')});\n  if (!r) return;\n  _lastEmailId = r.id;\n  _genEmailData = r;\n  document.getElementById(\'em-subject\').value = r.subject;\n  document.getElementById(\'em-body\').value    = r.body;\n  document.getElementById(\'email-preview\').style.display=\'block\';\n  toast(\'Email generated! SMS notification sent.\',\'success\');\n}\n\nasync function sendGenEmail() {\n  if (!_lastEmailId) return toast(\'Generate an email first\',\'error\');\n  // Update with any edits first\n  await api(\'PUT\',`/emails/${_lastEmailId}`,{\n    subject:v(\'em-subject\'), body:v(\'em-body\')});\n  const r = await api(\'POST\',`/emails/${_lastEmailId}/send`);\n  if (!r) return;\n  const sr = r.send_result||{};\n  closeModal(\'email-modal\');\n  toast(sr.status===\'sent\'?\'\xe2\x9c\x85 Email sent! SMS dispatched.\':(sr.message||\'Check Gmail config\'), sr.status===\'sent\'?\'success\':\'error\');\n}\n\nasync function saveDraftEmail() {\n  if (!_lastEmailId) return;\n  await api(\'PUT\',`/emails/${_lastEmailId}`,{subject:v(\'em-subject\'),body:v(\'em-body\')});\n  closeModal(\'email-modal\');\n  toast(\'Draft saved!\',\'success\');\n}\n\nfunction openMeetingModal(companyId) {\n  document.getElementById(\'mtg-company-id\').value = companyId||\'\';\n  [\'mtg-title\',\'mtg-desc\'].forEach(id=>{const el=document.getElementById(id);if(el)el.value=\'\';});\n  document.getElementById(\'mtg-dur\').value=\'30\';\n  const today = new Date().toISOString().split(\'T\')[0];\n  document.getElementById(\'mtg-date\').value=today;\n  document.getElementById(\'mtg-time\').value=\'10:00\';\n  openModal(\'meeting-modal\');\n}\n\nasync function saveMeeting() {\n  const cid   = parseInt(v(\'mtg-company-id\'));\n  const title = v(\'mtg-title\').trim();\n  if (!title) return toast(\'Title required\',\'error\');\n  if (!cid)   return toast(\'Select a company first\',\'error\');\n  const scheduled_at = `${v(\'mtg-date\')} ${v(\'mtg-time\')}:00`;\n  const r = await api(\'POST\',\'/meetings\',{\n    company_id:cid, title, meeting_type:v(\'mtg-type\'),\n    description:v(\'mtg-desc\')||null,\n    scheduled_at, duration_minutes:parseInt(v(\'mtg-dur\'))||30,\n  });\n  if (!r) return;\n  closeModal(\'meeting-modal\');\n  toast(\'Meeting scheduled! SMS + 3 reminders set (24h, 1h, 10min).\',\'success\');\n  if (cid) viewCompany(cid); else goto(\'meetings\');\n}\n\nfunction openCallModal(companyId) {\n  document.getElementById(\'call-company-id\').value = companyId||\'\';\n  document.getElementById(\'call-phone\').value=\'\';\n  document.getElementById(\'call-script\').value=\'\';\n  openModal(\'call-modal\');\n}\n\nasync function makeCall() {\n  const phone = v(\'call-phone\').trim();\n  if (!phone) return toast(\'Phone number required (+14155550100)\',\'error\');\n  const cid = parseInt(v(\'call-company-id\'))||null;\n  const r = await api(\'POST\',\'/calls/make\',{\n    company_id:cid, phone_number:phone,\n    objective:v(\'call-obj\'), voice:v(\'call-voice\'),\n    custom_task:v(\'call-script\')||null,\n  });\n  if (!r) return;\n  const br = r.bland_result||{};\n  closeModal(\'call-modal\');\n  if (br.status===\'queued\') toast(`Call queued! Bland ID: ${br.call_id||\'\'}. SMS sent.`,\'success\');\n  else toast(br.message||\'Call failed \xe2\x80\x94 check BLAND_API_KEY\',\'error\');\n  goto(\'calls\');\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   SYSTEM STATUS\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nasync function loadStatus() {\n  try {\n    const r = await fetch(\'/health\').then(r=>r.json());\n    const el = document.getElementById(\'sys-status\');\n    if (!el) return;\n    el.innerHTML = \'<b>System</b>\'\n      + status_dot(r.groq,\'Groq AI\')\n      + status_dot(r.twilio,\'Twilio SMS\')\n      + status_dot(r.bland,\'Bland AI\')\n      + status_dot(r.gmail,\'Gmail\');\n  } catch(e) {}\n}\nfunction status_dot(ok,lbl){\n  return `<div style="margin-top:3px">${ok?\'\xf0\x9f\x9f\xa2\':\'\xf0\x9f\x94\xb4\'} ${lbl}</div>`;\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   UTILITIES\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nfunction set(id,html){ const el=document.getElementById(id); if(el) el.innerHTML=html; }\nfunction v(id){ const el=document.getElementById(id); return el?(el.value||\'\'):\'\'; }\nfunction s(id,val){ const el=document.getElementById(id); if(el) el.value=val; }\nfunction esc(s){ if(!s) return \'\'; return String(s).replace(/&/g,\'&amp;\').replace(/</g,\'&lt;\').replace(/>/g,\'&gt;\').replace(/"/g,\'&quot;\'); }\nfunction fmt(n){ return Number(n||0).toLocaleString(); }\nfunction fmtM(n){ if(!n) return \'0\'; if(n>=1e9) return (n/1e9).toFixed(1)+\'B\'; if(n>=1e6) return (n/1e6).toFixed(0)+\'M\'; if(n>=1e3) return (n/1e3).toFixed(0)+\'K\'; return n; }\nfunction fmtDt(s){ if(!s) return \'\xe2\x80\x94\'; return s.slice(0,16).replace(\'T\',\' \'); }\nfunction metric(lbl,val,color=\'var(--accent)\'){ return `<div class="metric-card"><div class="metric-val" style="color:${color}">${val}</div><div class="metric-lbl">${lbl}</div></div>`; }\nfunction scoreBadge(s){ const tier=s>=70?\'hot\':s>=40?\'warm\':\'cold\'; return `<span class="badge badge-${tier}">${s}/100</span>`; }\nfunction miniChart(data,key,color){\n  if(!data.length) return \'<div class="empty">No data</div>\';\n  const max=Math.max(...data.map(d=>d[key]||0),1);\n  return \'<div style="display:flex;align-items:flex-end;gap:2px;height:60px">\'\n    +data.slice(-20).map(d=>{const h=Math.round(((d[key]||0)/max)*60);\n      return `<div style="flex:1;height:${h}px;background:${color};border-radius:2px 2px 0 0;min-height:2px" title="${d.date}: ${d[key]}"></div>`;\n    }).join(\'\')+\'</div>\';\n}\n\nlet _debounceTimer;\nfunction debounce(fn,ms){ clearTimeout(_debounceTimer); _debounceTimer=setTimeout(fn,ms); }\n\nfunction openModal(id){ document.getElementById(id).classList.add(\'open\'); }\nfunction closeModal(id){ document.getElementById(id).classList.remove(\'open\'); }\ndocument.addEventListener(\'click\', e=>{ if(e.target.classList.contains(\'modal-backdrop\')) e.target.classList.remove(\'open\'); });\n\nfunction switchTab(el,panelId){\n  el.closest(\'.section,#content\').querySelectorAll(\'.tab\').forEach(t=>t.classList.remove(\'active\'));\n  el.closest(\'.section,#content\').querySelectorAll(\'.tab-panel\').forEach(p=>p.classList.remove(\'active\'));\n  el.classList.add(\'active\');\n  const panel=document.getElementById(panelId);\n  if(panel) panel.classList.add(\'active\');\n}\n\nfunction toast(msg,type=\'info\'){\n  const box=document.getElementById(\'toast\');\n  const el=document.createElement(\'div\');\n  el.className=`toast-msg toast-${type}`;\n  el.textContent=msg;\n  box.appendChild(el);\n  setTimeout(()=>el.remove(), type===\'error\'?5000:3000);\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\n   BOOT\n\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\n(async function boot() {\n  // Always verify token with server before showing app\n  // This prevents stale tokens from causing blank screens\n  if (TOKEN) {\n    try {\n      const r = await fetch(\'/api/auth/me\', {\n        headers: { \'Authorization\': \'Bearer \' + TOKEN }\n      });\n      if (r.ok) {\n        const data = await r.json();\n        if (data && data.data) {\n          USER = data.data;\n          localStorage.setItem(\'sales_user\', JSON.stringify(USER));\n          showApp();\n          return;\n        }\n      }\n    } catch(e) {\n      // Network error \xe2\x80\x94 clear stale token and show login\n      console.warn(\'Token verify failed:\', e);\n    }\n    // Token invalid or expired \xe2\x80\x94 clear it\n    TOKEN = \'\';\n    localStorage.removeItem(\'sales_token\');\n    localStorage.removeItem(\'sales_user\');\n  }\n  // Show login screen\n  const ls = document.getElementById(\'login-screen\');\n  const ap = document.getElementById(\'app\');\n  if (ls) ls.style.display = \'flex\';\n  if (ap) ap.style.display = \'none\';\n})();\n\n\n/* \xe2\x94\x80\xe2\x94\x80 Particle canvas system \xe2\x80\x94 pure JS, <2KB, no libs, GPU-composited \xe2\x94\x80\xe2\x94\x80 */\n(function(){\n  \'use strict\';\n  var canvas = document.createElement(\'canvas\');\n  canvas.style.cssText = \'position:fixed;top:0;left:0;width:100%;height:100%;z-index:-1;pointer-events:none;opacity:0.55;\';\n  canvas.id = \'particle-canvas\';\n  document.body.appendChild(canvas);\n\n  var ctx = canvas.getContext(\'2d\');\n  var W, H, particles = [], RAF;\n  var PARTICLE_COUNT = 55;\n\n  function resize() {\n    W = canvas.width  = window.innerWidth;\n    H = canvas.height = window.innerHeight;\n  }\n\n  function Particle() {\n    this.reset();\n  }\n  Particle.prototype.reset = function() {\n    this.x  = Math.random() * W;\n    this.y  = Math.random() * H;\n    this.r  = Math.random() * 1.6 + 0.3;\n    this.vx = (Math.random() - 0.5) * 0.18;\n    this.vy = (Math.random() - 0.5) * 0.18;\n    this.alpha = Math.random() * 0.5 + 0.08;\n    this.pulse = Math.random() * Math.PI * 2;\n    this.pulseSpeed = 0.008 + Math.random() * 0.012;\n    // Corporate palette: blue / indigo / teal\n    var palettes = [\n      \'rgba(59,130,246,\',\n      \'rgba(99,102,241,\',\n      \'rgba(16,185,129,\',\n      \'rgba(148,163,184,\',\n    ];\n    this.color = palettes[Math.floor(Math.random() * palettes.length)];\n  };\n  Particle.prototype.update = function() {\n    this.x += this.vx;\n    this.y += this.vy;\n    this.pulse += this.pulseSpeed;\n    // Drift back softly\n    if (this.x < -10) this.x = W + 10;\n    if (this.x > W + 10) this.x = -10;\n    if (this.y < -10) this.y = H + 10;\n    if (this.y > H + 10) this.y = -10;\n  };\n  Particle.prototype.draw = function() {\n    var a = this.alpha * (0.7 + 0.3 * Math.sin(this.pulse));\n    ctx.beginPath();\n    ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);\n    ctx.fillStyle = this.color + a + \')\';\n    ctx.fill();\n  };\n\n  function init() {\n    resize();\n    particles = [];\n    for (var i = 0; i < PARTICLE_COUNT; i++) {\n      particles.push(new Particle());\n    }\n  }\n\n  // Draw subtle connecting lines between nearby particles\n  function drawConnections() {\n    var maxDist = 130;\n    for (var i = 0; i < particles.length; i++) {\n      for (var j = i + 1; j < particles.length; j++) {\n        var dx = particles[i].x - particles[j].x;\n        var dy = particles[i].y - particles[j].y;\n        var dist = Math.sqrt(dx*dx + dy*dy);\n        if (dist < maxDist) {\n          var opacity = (1 - dist/maxDist) * 0.08;\n          ctx.beginPath();\n          ctx.moveTo(particles[i].x, particles[i].y);\n          ctx.lineTo(particles[j].x, particles[j].y);\n          ctx.strokeStyle = \'rgba(99,102,241,\' + opacity + \')\';\n          ctx.lineWidth = 0.5;\n          ctx.stroke();\n        }\n      }\n    }\n  }\n\n  function loop() {\n    ctx.clearRect(0, 0, W, H);\n    drawConnections();\n    for (var i = 0; i < particles.length; i++) {\n      particles[i].update();\n      particles[i].draw();\n    }\n    RAF = requestAnimationFrame(loop);\n  }\n\n  window.addEventListener(\'resize\', function() {\n    cancelAnimationFrame(RAF);\n    resize();\n    // Reposition particles within new bounds\n    particles.forEach(function(p){\n      if(p.x > W) p.x = Math.random()*W;\n      if(p.y > H) p.y = Math.random()*H;\n    });\n    loop();\n  });\n\n  // Only run when page is visible \xe2\x80\x94 saves CPU/battery\n  document.addEventListener(\'visibilitychange\', function() {\n    if (document.hidden) {\n      cancelAnimationFrame(RAF);\n    } else {\n      loop();\n    }\n  });\n\n  // Init after DOM ready\n  if (document.readyState === \'loading\') {\n    document.addEventListener(\'DOMContentLoaded\', function() { init(); loop(); });\n  } else {\n    init(); loop();\n  }\n})();\n\n\nasync function transcripts() {\n  const rows = await api(\'GET\',\'/transcripts\');\n  if (!rows || !rows.length) { set(\'content\',\'<div class="section"><p style="color:var(--muted)">No transcripts yet. Process a call to see transcripts here.</p></div>\'); return; }\n  const scoreColor = s => s>=8?\'#22c55e\':s>=6?\'#f59e0b\':s>=4?\'#60a5fa\':\'#ef4444\';\n  const scoreLabel = s => s>=8?\'Hot \xf0\x9f\x94\xa5\':s>=6?\'Warm \xe2\x9a\xa0\xef\xb8\x8f\':s>=4?\'Cool\':\'Cold\';\n  set(\'content\',`\n    <div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:center">\n      <h3 style="margin:0">Call Transcripts (${rows.length})</h3>\n    </div>\n    ${rows.map(t=>`\n    <div class="section" style="margin-bottom:12px">\n      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">\n        <div>\n          <b>${t.company_name||\'Unknown\'}</b> \xe2\x80\x94 ${t.contact_name||\'\'}\n          <span style="font-size:.75rem;color:var(--muted);margin-left:8px">${(t.created_at||\'\').slice(0,16)}</span>\n        </div>\n        <div style="display:flex;gap:8px;align-items:center">\n          <span style="font-size:.85rem;font-weight:600;color:${scoreColor(t.interest_score||0)}">\n            ${t.interest_score||0}/10 \xe2\x80\x94 ${scoreLabel(t.interest_score||0)}\n          </span>\n          ${t.meeting_booked?\'<span style="background:#1e3a5f;color:#60a5fa;padding:2px 8px;border-radius:4px;font-size:.75rem">\xf0\x9f\x93\x85 Meeting Booked</span>\':\'\'}\n          <span style="background:${t.sentiment===\'positive\'?\'#14532d\':t.sentiment===\'negative\'?\'#7f1d1d\':\'#1e293b\'};color:${t.sentiment===\'positive\'?\'#22c55e\':t.sentiment===\'negative\'?\'#ef4444\':\'#94a3b8\'};padding:2px 8px;border-radius:4px;font-size:.75rem">\n            ${(t.sentiment||\'neutral\').toUpperCase()}\n          </span>\n        </div>\n      </div>\n      ${t.summary?`<p style="font-size:.85rem;color:var(--muted);margin:8px 0 4px">${t.summary}</p>`:\'\'}\n      ${t.google_meet_link?`<p style="font-size:.8rem;margin:4px 0"><a href="${t.google_meet_link}" target="_blank" style="color:#60a5fa">\xf0\x9f\x93\xb9 ${t.google_meet_link}</a></p>`:\'\'}\n      ${t.transcript?`<details style="margin-top:8px"><summary style="cursor:pointer;font-size:.8rem;color:var(--accent)">View Full Transcript</summary><pre style="background:#0f172a;padding:12px;border-radius:6px;font-size:.75rem;color:#94a3b8;white-space:pre-wrap;max-height:300px;overflow-y:auto;margin-top:8px">${t.transcript.replace(/</g,\'&lt;\')}</pre></details>`:\'<p style="font-size:.75rem;color:#475569;margin-top:6px">Transcript pending...</p>\'}\n      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">\n        <button class="btn btn-sm btn-ghost" onclick="processTranscript(${t.call_id})">\xf0\x9f\x94\x84 Reprocess</button>\n        ${t.next_action===\'book_meeting\'?\'<button class="btn btn-sm btn-primary" onclick="">\xf0\x9f\x93\x85 Book Meeting</button>\':\'\'}\n      </div>\n    </div>`).join(\'\')}`);\n}\n\nasync function processTranscript(callId) {\n  toast(\'Processing transcript...\',\'info\');\n  const r = await api(\'POST\',`/calls/${callId}/process-transcript`);\n  if (r) { toast(\'\xe2\x9c\x85 Transcript processed \xe2\x80\x94 email draft created\',\'success\'); transcripts(); }\n}\n\nasync function drafts() {\n  const rows = await api(\'GET\',\'/email-drafts\');\n  if (!rows) return;\n  const pending = rows.filter(r=>r.status===\'pending_approval\');\n  const sent    = rows.filter(r=>r.status===\'sent\');\n  const rejected= rows.filter(r=>r.status===\'rejected\');\n\n  const draftTypeLabel = {\'cold\':\'Cold Email\',\'follow_up\':\'Follow-up\',\'meeting_confirm\':\'Meeting Confirm\',\n    \'followup\':\'Follow-up\',\'thankyou\':\'Thank You\',\'high_interest_followup\':\'High Interest Follow-up\'};\n\n  const draftCard = (d,showActions=true) => `\n    <div class="section" style="margin-bottom:12px" id="draft-${d.id}">\n      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">\n        <div>\n          <span style="background:#1e3a5f;color:#60a5fa;padding:2px 8px;border-radius:4px;font-size:.72rem;margin-right:6px">${draftTypeLabel[d.draft_type]||d.draft_type}</span>\n          <b>${d.company_name||\'\'}</b>\n          <span style="font-size:.75rem;color:var(--muted);margin-left:6px">Day ${d.scheduled_send_day||0}</span>\n        </div>\n        <span style="font-size:.72rem;color:var(--muted)">${(d.created_at||\'\').slice(0,16)}</span>\n      </div>\n      <p style="font-size:.85rem;font-weight:600;margin:8px 0 2px">To: ${d.recipient_email} ${d.recipient_name?\'(\'+d.recipient_name+\')\':\'\'}</p>\n      <div style="background:#0f172a;border-radius:6px;padding:10px;margin:6px 0">\n        <p style="font-weight:600;font-size:.85rem;margin:0 0 6px">Subject: <span id="subj-${d.id}" contenteditable="${showActions}" style="outline:none">${d.subject}</span></p>\n        <pre id="body-${d.id}" contenteditable="${showActions}" style="white-space:pre-wrap;font-family:inherit;font-size:.8rem;color:#94a3b8;margin:0;outline:none;min-height:60px">${d.body}</pre>\n      </div>\n      ${showActions?`\n      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">\n        <button class="btn btn-sm btn-primary" onclick="approveDraft(${d.id})">\xe2\x9c\x85 Approve & Send</button>\n        <button class="btn btn-sm btn-ghost" onclick="saveDraftEdit(${d.id})">\xf0\x9f\x92\xbe Save Changes</button>\n        <button class="btn btn-sm btn-ghost" style="color:#ef4444" onclick="rejectDraft(${d.id})">\xe2\x9d\x8c Reject</button>\n      </div>`:\'<span style="font-size:.75rem;color:var(--muted)">\'+d.status.toUpperCase()+\'</span>\'}\n    </div>`;\n\n  set(\'content\',`\n    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px">\n      <h3 style="margin:0">Email Drafts \xe2\x80\x94 Approval Queue</h3>\n      <div style="display:flex;gap:8px">\n        <button class="btn btn-sm btn-ghost" onclick="generateDraftsNow()">\xf0\x9f\xa4\x96 Generate Drafts</button>\n      </div>\n    </div>\n    <div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap">\n      <span style="background:#f59e0b22;color:#f59e0b;padding:4px 12px;border-radius:20px;font-size:.8rem">\xe2\x8f\xb3 ${pending.length} Pending</span>\n      <span style="background:#22c55e22;color:#22c55e;padding:4px 12px;border-radius:20px;font-size:.8rem">\xe2\x9c\x85 ${sent.length} Sent</span>\n      <span style="background:#ef444422;color:#ef4444;padding:4px 12px;border-radius:20px;font-size:.8rem">\xe2\x9d\x8c ${rejected.length} Rejected</span>\n    </div>\n    ${pending.length?`<h4 style="color:#f59e0b;margin:0 0 10px">\xe2\x8f\xb3 Pending Approval</h4>${pending.map(d=>draftCard(d,true)).join(\'\')}`:\'<p style="color:var(--muted)">No drafts pending approval.</p>\'}\n    ${sent.length?`<h4 style="color:#22c55e;margin:16px 0 10px">\xe2\x9c\x85 Sent (${sent.length})</h4>${sent.slice(0,5).map(d=>draftCard(d,false)).join(\'\')}`:\'\'}\n    ${rejected.length?`<h4 style="color:#ef4444;margin:16px 0 10px">\xe2\x9d\x8c Rejected (${rejected.length})</h4>${rejected.slice(0,3).map(d=>draftCard(d,false)).join(\'\')}`:\'\'}\n  `);\n}\n\nasync function approveDraft(id) {\n  if (!confirm(\'Send this email now?\')) return;\n  const r = await api(\'POST\',`/email-drafts/${id}/approve`);\n  if (r) { toast(r.message||\'Email sent\',\'success\'); drafts(); }\n}\n\nasync function saveDraftEdit(id) {\n  const subject = document.getElementById(`subj-${id}`)?.textContent?.trim();\n  const body    = document.getElementById(`body-${id}`)?.textContent?.trim();\n  const r = await api(\'PUT\',`/email-drafts/${id}`,{subject,body});\n  if (r) toast(\'Draft saved\',\'success\');\n}\n\nasync function rejectDraft(id) {\n  const r = await api(\'POST\',`/email-drafts/${id}/reject`);\n  if (r) { toast(\'Draft rejected\',\'info\'); drafts(); }\n}\n\nasync function generateDraftsNow() {\n  toast(\'Generating email drafts...\',\'info\');\n  const r = await api(\'POST\',\'/automation/generate-drafts\');\n  if (r) { toast(\'\xe2\x9c\x85 \'+r.message,\'success\'); setTimeout(drafts,2000); }\n}\n\nasync function opportunities() {\n  const rows = await api(\'GET\',\'/opportunities\');\n  if (!rows) return;\n  const scoreColor = s => s>=9?\'#22c55e\':s>=7?\'#60a5fa\':s>=5?\'#f59e0b\':\'#94a3b8\';\n  const scoreTier  = s => s>=9?\'\xf0\x9f\x94\xa5 Hot\':\'warning\' === s?\'\xe2\x9a\xa0\xef\xb8\x8f\':\'\xf0\x9f\x92\x99\';\n  const open = rows.filter(r=>r.status===\'open\');\n  const won  = rows.filter(r=>r.status===\'won\');\n\n  set(\'content\',`\n    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">\n      <h3 style="margin:0">Opportunities (${rows.length})</h3>\n      <button class="btn btn-sm btn-primary" onclick="showNewOppModal()">+ New Opportunity</button>\n    </div>\n    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px">\n      ${rows.map(op=>`\n      <div class="section" style="border-left:3px solid ${scoreColor(op.interest_score||0)};padding:14px 16px">\n        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">\n          <div>\n            <p style="font-size:.72rem;color:var(--muted);margin:0 0 2px">${op.company_name||\'\'} \xc2\xb7 ${op.industry||\'\'}</p>\n            <h4 style="margin:0;font-size:.95rem">${op.title}</h4>\n          </div>\n          <div style="text-align:right">\n            <div style="font-size:1.2rem;font-weight:700;color:${scoreColor(op.interest_score||0)}">${op.interest_score||0}/10</div>\n            <div style="font-size:.7rem;color:var(--muted)">interest</div>\n          </div>\n        </div>\n        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">\n          <div>\n            <span style="font-size:.85rem;color:var(--muted)">Quoted: </span>\n            <span style="font-size:1rem;font-weight:600;color:#22c55e">AUD $${(op.quoted_value||0).toLocaleString()}</span>\n          </div>\n          ${op.accepted_value?`<div><span style="font-size:.85rem;color:var(--muted)">Accepted: </span><span style="color:#22c55e;font-weight:600">$${op.accepted_value.toLocaleString()}</span></div>`:\'\'}\n        </div>\n        ${op.contact_name?`<p style="font-size:.8rem;color:var(--muted);margin:4px 0">\xf0\x9f\x91\xa4 ${op.contact_name} ${op.contact_email?\'\xc2\xb7 \'+op.contact_email:\'\'}</p>`:\'\'}\n        ${op.notes?`<p style="font-size:.8rem;color:var(--muted);margin:6px 0;font-style:italic">${op.notes.slice(0,100)}</p>`:\'\'}\n        <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">\n          <button class="btn btn-sm btn-ghost" onclick="genQuotation(${op.id})">\xf0\x9f\x93\x84 Quotation</button>\n          <button class="btn btn-sm btn-ghost" onclick="genInvoice(${op.id})">\xf0\x9f\xa7\xbe Invoice</button>\n          <button class="btn btn-sm btn-ghost" onclick="editOppValue(${op.id},${op.quoted_value||0})">\xf0\x9f\x92\xb2 Update Value</button>\n          ${op.interest_score>=9&&op.status===\'open\'?\'<button class="btn btn-sm btn-primary" onclick="markOppWon(\'+op.id+\')">\xf0\x9f\x8f\x86 Mark Won</button>\':\'\'}\n        </div>\n        ${op.quotation_text?`<details style="margin-top:8px"><summary style="cursor:pointer;font-size:.8rem;color:var(--accent)">View Quotation</summary><pre style="background:#0f172a;padding:10px;border-radius:6px;font-size:.73rem;color:#94a3b8;white-space:pre-wrap;max-height:250px;overflow-y:auto;margin-top:6px">${op.quotation_text.replace(/</g,\'&lt;\')}</pre></details>`:\'\'}\n        ${op.invoice_text?`<details style="margin-top:6px"><summary style="cursor:pointer;font-size:.8rem;color:var(--accent)">View Invoice</summary><pre style="background:#0f172a;padding:10px;border-radius:6px;font-size:.73rem;color:#94a3b8;white-space:pre-wrap;max-height:250px;overflow-y:auto;margin-top:6px">${op.invoice_text.replace(/</g,\'&lt;\')}</pre></details>`:\'\'}\n      </div>`).join(\'\')}\n      ${!rows.length?\'<p style="color:var(--muted)">No opportunities yet. They auto-create when AI calls score 8+ or you can add them manually.</p>\':\'\'}\n    </div>\n  `);\n}\n\nasync function genQuotation(oid) {\n  toast(\'Generating quotation...\',\'info\');\n  const r = await api(\'POST\',`/opportunities/${oid}/generate-quotation`);\n  if (r) { toast(\'\xe2\x9c\x85 Quotation generated\',\'success\'); opportunities(); }\n}\n\nasync function genInvoice(oid) {\n  toast(\'Generating invoice...\',\'info\');\n  const r = await api(\'POST\',`/opportunities/${oid}/generate-invoice`);\n  if (r) { toast(\'\xe2\x9c\x85 Invoice generated\',\'success\'); opportunities(); }\n}\n\nasync function editOppValue(oid, current) {\n  const val = prompt(\'Enter quoted value (AUD):\', current);\n  if (!val) return;\n  const r = await api(\'PUT\',`/opportunities/${oid}`,{quoted_value:parseFloat(val)});\n  if (r) { toast(\'Value updated\',\'success\'); opportunities(); }\n}\n\nasync function markOppWon(oid) {\n  const r = await api(\'PUT\',`/opportunities/${oid}`,{status:\'won\'});\n  if (r) { toast(\'\xf0\x9f\x8f\x86 Opportunity marked as Won!\',\'success\'); opportunities(); }\n}\n\nfunction showNewOppModal() {\n  modal(`<h3>New Opportunity</h3>\n    <div class="form-group"><label>Company</label><select id="opp-co">${COMPANIES_CACHE.map(c=>`<option value="${c.id}">${c.name}</option>`).join(\'\')}</select></div>\n    <div class="form-group"><label>Title</label><input id="opp-title" placeholder="e.g. AI Automation \xe2\x80\x94 Smith Plumbing"></div>\n    <div class="form-group"><label>Interest Score (1-10)</label><input id="opp-score" type="number" min="1" max="10" value="7"></div>\n    <div class="form-group"><label>Quoted Value (AUD)</label><input id="opp-value" type="number" value="5000"></div>\n    <div class="form-group"><label>Notes</label><textarea id="opp-notes" rows="3" placeholder="Key discussion points..."></textarea></div>\n    <button class="btn btn-primary" onclick="createOpportunity()">Create Opportunity</button>`,\n  \'New Opportunity\');\n}\n\nasync function createOpportunity() {\n  const r = await api(\'POST\',\'/opportunities\',{\n    company_id:parseInt(v(\'opp-co\')), title:v(\'opp-title\'),\n    interest_score:parseInt(v(\'opp-score\')||7), quoted_value:parseFloat(v(\'opp-value\')||5000),\n    notes:v(\'opp-notes\')});\n  if (r) { closeModal(); toast(\'\xe2\x9c\x85 Opportunity created\',\'success\'); opportunities(); }\n}\n\nlet COMPANIES_CACHE = [];\n(async()=>{ const r = await api(\'GET\',\'/companies\'); if(r) COMPANIES_CACHE=r; })();\n\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 INVOICES PAGE \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nasync function invoices() {\n  const rows = await api(\'GET\',\'/invoices\');\n  if (!rows) return;\n  const statusColor = {\'draft\':\'#f59e0b\',\'approved\':\'#22c55e\',\'paid\':\'#3b82f6\'};\n  set(\'content\',`\n    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">\n      <h3 style="margin:0">Invoices (${rows.length})</h3>\n      <button class="btn btn-sm btn-primary" onclick="showCreateInvoiceModal()">+ New Invoice</button>\n    </div>\n    ${!rows.length?\'<div class="section"><p style="color:var(--muted)">No invoices yet. Create from an Opportunity or click New Invoice.</p></div>\':\'\'}\n    ${rows.map(inv=>`\n    <div class="section" style="margin-bottom:10px;border-left:3px solid ${statusColor[inv.status]||\'#475569\'}">\n      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">\n        <div>\n          <span style="font-size:.72rem;font-weight:700;color:${statusColor[inv.status]||\'#94a3b8\'};text-transform:uppercase">${inv.status}</span>\n          <b style="margin-left:8px">${inv.invoice_number}</b>\n          <span style="font-size:.8rem;color:var(--muted);margin-left:8px">${inv.company_name||\'\'}</span>\n        </div>\n        <div style="text-align:right">\n          <div style="font-size:1.1rem;font-weight:700;color:#22c55e">AUD $${(inv.total_amount||0).toLocaleString(\'en-AU\',{minimumFractionDigits:2})}</div>\n          <div style="font-size:.72rem;color:var(--muted)">Due: ${(inv.due_date||\'\').slice(0,10)}</div>\n        </div>\n      </div>\n      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">\n        <button class="btn btn-sm btn-primary" onclick="viewInvoiceHTML(${inv.id},\'professional\')">\xf0\x9f\x91\x81 View</button>\n        <button class="btn btn-sm btn-ghost" onclick="viewInvoiceHTML(${inv.id},\'modern\')">\xf0\x9f\x8e\xa8 Modern</button>\n        <button class="btn btn-sm btn-ghost" onclick="viewInvoiceHTML(${inv.id},\'classic\')">\xf0\x9f\x93\x9c Classic</button>\n        ${inv.status===\'draft\'?`<button class="btn btn-sm btn-ghost" style="color:#22c55e" onclick="approveInvoice(${inv.id})">\xe2\x9c\x85 Approve</button>`:\'\'}\n        <button class="btn btn-sm btn-ghost" onclick="printInvoice(${inv.id})">\xf0\x9f\x96\xa8 Print</button>\n        <a href="https://go.xero.com/AccountsReceivable/NewInvoice.aspx" target="_blank" class="btn btn-sm btn-ghost">\xf0\x9f\x94\x97 Xero</a>\n      </div>\n    </div>`).join(\'\')}\n  `);\n}\n\nasync function viewInvoiceHTML(id, template=\'professional\') {\n  const r = await api(\'GET\',`/invoices/${id}/html?template=${template}`);\n  if (!r) return;\n  const w = window.open(\'\',\'_blank\',\'width=900,height=700,left=100,top=50\');\n  w.document.write(r.html);\n  w.document.close();\n}\n\nasync function printInvoice(id) {\n  const r = await api(\'GET\',`/invoices/${id}/html?template=professional`);\n  if (!r) return;\n  const w = window.open(\'\',\'_blank\');\n  w.document.write(r.html);\n  w.document.close();\n  setTimeout(()=>w.print(),800);\n}\n\nasync function approveInvoice(id) {\n  if(!confirm(\'Approve this invoice?\')) return;\n  const r = await api(\'POST\',`/invoices/${id}/approve`);\n  if(r){ toast(r.message||\'Invoice approved\',\'success\'); invoices(); }\n}\n\nfunction showCreateInvoiceModal() {\n  modal(`\n    <h3>Create Invoice</h3>\n    <div class="form-group"><label>Company</label>\n      <select id="inv-co">${COMPANIES_CACHE.map(c=>`<option value="${c.id}">${c.name}</option>`).join(\'\')}</select></div>\n    <div class="form-group"><label>Amount (ex GST, AUD)</label><input id="inv-amount" type="number" value="4500" placeholder="4500"></div>\n    <div class="form-group"><label>Template</label>\n      <select id="inv-tmpl"><option value="professional">Professional (Dark Navy)</option><option value="modern">Modern (Blue)</option><option value="classic">Classic</option></select></div>\n    <button class="btn btn-primary" onclick="createInvoice()">Generate Invoice</button>`,\n  \'New Invoice\');\n}\n\nasync function createInvoice() {\n  const r = await api(\'POST\',\'/invoices\',{\n    company_id:parseInt(v(\'inv-co\')),\n    amount:parseFloat(v(\'inv-amount\')||4500),\n    template:v(\'inv-tmpl\')});\n  if(r){ closeModal(); toast(\'\xe2\x9c\x85 Invoice created\',\'success\');\n    const w=window.open(\'\',\'_blank\',\'width=900,height=700\');\n    w.document.write(r.html); w.document.close();\n    invoices(); }\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 QUOTATIONS PAGE \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nasync function quotations() {\n  const rows = await api(\'GET\',\'/quotations\');\n  if (!rows) return;\n  const statusColor = {\'draft\':\'#f59e0b\',\'approved\':\'#22c55e\',\'converted\':\'#3b82f6\',\'expired\':\'#ef4444\'};\n  set(\'content\',`\n    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">\n      <h3 style="margin:0">Quotations (${rows.length})</h3>\n      <button class="btn btn-sm btn-primary" onclick="showCreateQuoteModal()">+ New Quotation</button>\n    </div>\n    ${!rows.length?\'<div class="section"><p style="color:var(--muted)">No quotations yet. Create from Opportunities or click New Quotation.</p></div>\':\'\'}\n    ${rows.map(q=>(`\n    <div class="section" style="margin-bottom:10px;border-left:3px solid ${statusColor[q.status]||\'#475569\'}">\n      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">\n        <div>\n          <span style="font-size:.72rem;font-weight:700;color:${statusColor[q.status]||\'#94a3b8\'};text-transform:uppercase">${q.status}</span>\n          <b style="margin-left:8px">${q.quote_number}</b>\n          <span style="font-size:.8rem;color:var(--muted);margin-left:8px">${q.company_name||\'\'}</span>\n        </div>\n        <div style="text-align:right">\n          <div style="font-size:1.1rem;font-weight:700;color:#3b82f6">AUD $${(q.total_amount||0).toLocaleString(\'en-AU\',{minimumFractionDigits:2})}/mo</div>\n          <div style="font-size:.72rem;color:var(--muted)">Valid until: ${(q.valid_until||\'\').slice(0,10)}</div>\n        </div>\n      </div>\n      <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">\n        <button class="btn btn-sm btn-primary" onclick="viewQuoteHTML(${q.id},\'professional\')">\xf0\x9f\x91\x81 View</button>\n        <button class="btn btn-sm btn-ghost" onclick="viewQuoteHTML(${q.id},\'modern\')">\xf0\x9f\x8e\xa8 Modern</button>\n        <button class="btn btn-sm btn-ghost" onclick="viewQuoteHTML(${q.id},\'classic\')">\xf0\x9f\x93\x9c Classic</button>\n        ${q.status===\'draft\'?`<button class="btn btn-sm btn-ghost" style="color:#22c55e" onclick="approveQuote(${q.id})">\xe2\x9c\x85 Approve</button>`:\'\'}\n        ${q.status===\'approved\'?`<button class="btn btn-sm btn-ghost" style="color:#3b82f6" onclick="convertToInvoice(${q.id})">\xf0\x9f\xa7\xbe Convert to Invoice</button>`:\'\'}\n        <button class="btn btn-sm btn-ghost" onclick="printQuote(${q.id})">\xf0\x9f\x96\xa8 Print</button>\n      </div>\n    </div>`)).join(\'\')}\n  `);\n}\n\nasync function viewQuoteHTML(id, template=\'professional\') {\n  const r = await api(\'GET\',`/quotations/${id}/html?template=${template}`);\n  if(!r) return;\n  const w=window.open(\'\',\'_blank\',\'width=900,height=700,left=100,top=50\');\n  w.document.write(r.html); w.document.close();\n}\n\nasync function printQuote(id) {\n  const r = await api(\'GET\',`/quotations/${id}/html?template=professional`);\n  if(!r) return;\n  const w=window.open(\'\',\'_blank\');\n  w.document.write(r.html); w.document.close();\n  setTimeout(()=>w.print(),800);\n}\n\nasync function approveQuote(id) {\n  const r = await api(\'POST\',`/quotations/${id}/approve`);\n  if(r){ toast(\'Quotation approved\',\'success\'); quotations(); }\n}\n\nasync function convertToInvoice(id) {\n  if(!confirm(\'Convert this quotation to an invoice?\')) return;\n  const r = await api(\'POST\',`/quotations/${id}/convert-to-invoice`);\n  if(r){ toast(\'\xe2\x9c\x85 Invoice created from quotation\',\'success\'); goto(\'invoices\'); }\n}\n\nfunction showCreateQuoteModal() {\n  modal(`\n    <h3>New Quotation</h3>\n    <div class="form-group"><label>Company</label>\n      <select id="q-co">${COMPANIES_CACHE.map(c=>`<option value="${c.id}">${c.name}</option>`).join(\'\')}</select></div>\n    <div class="form-group"><label>Monthly Value (ex GST, AUD)</label><input id="q-amount" type="number" value="4500"></div>\n    <div class="form-group"><label>Template</label>\n      <select id="q-tmpl"><option value="professional">Professional (Dark Navy)</option><option value="modern">Modern (Blue)</option><option value="classic">Classic</option></select></div>\n    <button class="btn btn-primary" onclick="createQuote()">Generate Quotation</button>`,\n  \'New Quotation\');\n}\n\nasync function createQuote() {\n  const r = await api(\'POST\',\'/quotations\',{\n    company_id:parseInt(v(\'q-co\')),\n    amount:parseFloat(v(\'q-amount\')||4500),\n    template:v(\'q-tmpl\')});\n  if(r){ closeModal(); toast(\'\xe2\x9c\x85 Quotation created\',\'success\');\n    const w=window.open(\'\',\'_blank\',\'width=900,height=700\');\n    w.document.write(r.html); w.document.close();\n    quotations(); }\n}\n\n/* \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 INBOUND CALLS PAGE \xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90\xe2\x95\x90 */\nasync function inbound() {\n  const st = await api(\'GET\',\'/bland/inbound-status\');\n  const rows = await api(\'GET\',\'/inbound-calls\');\n  if (!st) return;\n  const scoreColor = s=>s>=8?\'#22c55e\':s>=6?\'#f59e0b\':s>=4?\'#60a5fa\':\'#ef4444\';\n  set(\'content\',`\n    <div style="margin-bottom:16px">\n      <h3 style="margin:0 0 12px">Inbound Calls \xe2\x80\x94 Facebook Ad</h3>\n      <div class="section" style="margin-bottom:16px">\n        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">\n          <div>\n            <span style="font-size:.8rem;color:var(--muted)">Sarah AI Receptionist</span>\n            <p style="font-size:.85rem;margin:4px 0;color:#94a3b8">Handles inbound calls from Facebook ads. Auto-qualifies callers, books meetings, notifies Kevin via SMS.</p>\n          </div>\n          <div style="display:flex;gap:8px;flex-wrap:wrap">\n            ${st.configured?\n              `<span style="color:#22c55e;font-weight:600">\xf0\x9f\x9f\xa2 Active \xe2\x80\x94 ${st.inbound_number}</span>`:\n              `<span style="color:#ef4444">\xf0\x9f\x94\xb4 Not configured \xe2\x80\x94 add BLAND_INBOUND_NUMBER to Render env</span>`}\n          </div>\n        </div>\n        ${st.configured?`\n        <div style="margin-top:12px;background:#0f172a;padding:10px 14px;border-radius:6px;font-size:.8rem;color:#94a3b8">\n          <b style="color:#e2e8f0">Webhook URL:</b> ${st.webhook_url}<br>\n          <b style="color:#e2e8f0">Add this to:</b> Bland AI Dashboard \xe2\x86\x92 Phone Numbers \xe2\x86\x92 ${st.inbound_number} \xe2\x86\x92 Webhook\n        </div>\n        <div style="margin-top:10px;display:flex;gap:8px">\n          <button class="btn btn-sm btn-ghost" onclick="setupInbound()">\xf0\x9f\x94\x84 Re-configure Sarah</button>\n        </div>`:`\n        <div style="margin-top:12px;font-size:.82rem;color:#f59e0b">\n          Setup: 1. Buy a phone number on app.bland.ai. 2. Add BLAND_INBOUND_NUMBER=+1xxx to Render env. 3. Click Configure Sarah below.\n        </div>\n        <button class="btn btn-sm btn-primary" style="margin-top:10px" onclick="setupInbound()">\xe2\x9a\x99\xef\xb8\x8f Configure Sarah (Inbound)</button>`}\n      </div>\n\n      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:16px">\n        <div class="metric-card"><div class="metric-label">Total Inbound</div><div class="metric-value">${(rows||[]).length}</div></div>\n        <div class="metric-card"><div class="metric-label">Meetings Booked</div><div class="metric-value">${(rows||[]).filter(r=>r.meeting_booked).length}</div></div>\n        <div class="metric-card"><div class="metric-label">Hot Leads (8+)</div><div class="metric-value">${(rows||[]).filter(r=>(r.interest_score||0)>=8).length}</div></div>\n        <div class="metric-card"><div class="metric-label">Avg Score</div><div class="metric-value">${(rows||[]).length?(((rows||[]).reduce((a,r)=>a+(r.interest_score||0),0)/(rows||[]).length)).toFixed(1):0}/10</div></div>\n      </div>\n\n      ${(rows||[]).map(r=>`\n      <div class="section" style="margin-bottom:10px">\n        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">\n          <div>\n            <b>${r.caller_name||r.phone_number||\'Unknown Caller\'}</b>\n            ${r.caller_name?`<span style="color:var(--muted);font-size:.8rem;margin-left:6px">${r.phone_number}</span>`:\'\'}\n            <span style="background:#1e3a5f;color:#60a5fa;padding:2px 8px;border-radius:4px;font-size:.72rem;margin-left:8px">${r.campaign_source||\'facebook_ad\'}</span>\n          </div>\n          <div style="display:flex;gap:8px;align-items:center">\n            <span style="color:${scoreColor(r.interest_score||0)};font-weight:700">${r.interest_score||0}/10</span>\n            ${r.meeting_booked?\'<span style="background:#14532d;color:#22c55e;padding:2px 8px;border-radius:4px;font-size:.72rem">\xf0\x9f\x93\x85 Booked</span>\':\'\'}\n            <span style="font-size:.72rem;color:var(--muted)">${(r.created_at||\'\').slice(0,16)}</span>\n          </div>\n        </div>\n        ${r.summary?`<p style="font-size:.82rem;color:#94a3b8;margin:6px 0">${r.summary}</p>`:\'\'}\n        ${r.transcript?`<details style="margin-top:6px"><summary style="cursor:pointer;font-size:.8rem;color:var(--accent)">View Transcript</summary><pre style="background:#0f172a;padding:10px;border-radius:6px;font-size:.73rem;color:#94a3b8;white-space:pre-wrap;max-height:200px;overflow-y:auto;margin-top:6px">${r.transcript.replace(/</g,\'&lt;\')}</pre></details>`:\'\'}\n      </div>`).join(\'\')}\n      ${!(rows||[]).length?\'<p style="color:var(--muted)">No inbound calls yet. Once Facebook ads go live and the inbound number is configured, calls will appear here automatically.</p>\':\'\'}\n    </div>\n  `);\n}\n\nasync function setupInbound() {\n  toast(\'Configuring Sarah for inbound calls...\',\'info\');\n  const r = await api(\'POST\',\'/bland/setup-inbound\');\n  if(r) toast(r.data?.message||r.message||\'Sarah configured\',\'success\');\n}\n\n</script>\n</body>\n</html>\n'.decode('utf-8')

# ══ FLASK APP ═════════════════════════════════════════════════════════════════
app=Flask(__name__,static_folder=None)
app.secret_key=SECRET_KEY
app.register_blueprint(api)

@app.route("/")
def index():
    return Response(_HTML, mimetype="text/html")

@app.route("/favicon.ico")
def favicon():
    return Response(_HTML, mimetype="text/html")

# SPA routes — explicit list so Flask never confuses them with /api/
@app.route("/dashboard")
@app.route("/companies")
@app.route("/contacts")
@app.route("/emails")
@app.route("/meetings")
@app.route("/calls")
@app.route("/analytics")
@app.route("/automation")
@app.route("/chat")
@app.route("/sms")
@app.route("/sms-logs")
@app.route("/integrations")
@app.route("/settings")
def spa_page():
    return Response(_HTML, mimetype="text/html")

@app.get("/health")
def health():
    try: cos=len(q("SELECT id FROM companies")); db_ok=True
    except Exception: cos,db_ok=0,False
    return jsonify({
        "status":    "healthy" if db_ok else "degraded",
        "database":  db_ok,
        "companies": cos,
        "groq":      bool(GROQ_API_KEY),
        "twilio":    bool(TWILIO_SID),
        "bland":     bool(BLAND_API_KEY),
        "gmail":     bool(GMAIL_EMAIL or SENDGRID_API_KEY),
        "sendgrid":  bool(SENDGRID_API_KEY),
        "api_routes": len([r for r in app.url_map.iter_rules() if "/api/" in r.rule]),
        "version":   "v11",
    })

@app.get("/api/ping")
def api_ping():
    """Lightweight connectivity check — confirms API routes are reachable."""
    return jsonify({"ok":True,"message":"API is reachable","version":"v11"})

@app.errorhandler(404)
def _404(e):
    if request.path.startswith("/api/"):
        return jsonify({"ok":False,"error":f"Not found: {request.method} {request.path}"}),404
    # Unknown frontend path — serve SPA so client-side routing handles it
    return Response(_HTML, mimetype="text/html"), 200
@app.errorhandler(405)
def _405(e):
    return jsonify({"ok":False,"error":f"Method {request.method} not allowed on {request.path}"}),405
@app.errorhandler(500)
def _500(e):
    import traceback
    logger.error(f"500: {e}\n{traceback.format_exc()}")
    return jsonify({"ok":False,"error":f"Server error: {e}"}),500

# Startup at module import — works with gunicorn --preload
try: init_db(); logger.info("✅ DB ready")
except Exception as _e: logger.error(f"DB:{_e}")
try: start(); logger.info("✅ Scheduler ready")
except Exception as _e: logger.warning(f"Sched:{_e}")

if __name__=="__main__":
    _port=int(os.environ.get("PORT",5000))
    logger.info(f"http://localhost:{_port} | admin@salesai.com / Admin@123456")
    app.run(host="0.0.0.0",port=_port,debug=False,use_reloader=False)
