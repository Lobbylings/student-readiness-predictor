from werkzeug.utils import secure_filename
import os
import sqlite3
import joblib
import pandas as pd
import json
import re
import csv
import io
from flask import Response
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file
)


from werkzeug.security import generate_password_hash, check_password_hash

from content_utils import (
    extract_text_from_file,
    generate_questions_from_text,
    grade_all_answers_with_gemini,
    analyze_performance_with_gemini
)

load_dotenv()

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static")
)
app.config["UPLOAD_FOLDER"] = "uploads"
app.secret_key = os.environ.get("SECRET_KEY", "student_readiness_secret_key_2026")
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)



    
def get_stored_quiz_content():
    content_file = session.get("content_file")
    if not content_file:
        return ""

    if not os.path.exists(content_file):
        return ""

    try:
        with open(content_file, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def store_quiz_content(text):
    if not text or not text.strip():
        return None

    filename = f"quiz_content_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.txt"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)

    return filepath
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

model = joblib.load("student_readiness_model.pkl")
DB_PATH = os.path.join(os.path.dirname(__file__), "assessments.db")

if __name__ == "__main__":

    def create_users_table():
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            matric_number TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
    )
    """)

        conn.commit()
        conn.close()

def extract_json_payload(text):
    if not text:
        return None

    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    json_text = match.group(0)

    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None
# ----------------------------
# Database Helpers
# ----------------------------

def ensure_tables_exist_on_connection(conn):
    conn.execute("""
       CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    full_name TEXT,
    matric_number TEXT,
    department TEXT,
    level TEXT,
    course_code TEXT,
    course_title TEXT,
    study_hours INTEGER,
    failures INTEGER,
    missed_lectures INTEGER,
    g1 INTEGER,
    g2 INTEGER,
    confidence_level TEXT,
    revision_status TEXT,
    past_questions_practice TEXT,
    topic_understanding TEXT,
    prediction_text TEXT,
    confidence_score TEXT,
    interpretation TEXT,
    created_at TEXT,
    pretest_score INTEGER,
    pretest_level TEXT,
    overall_insight TEXT,

    quiz_score INTEGER DEFAULT 0,
    quiz_analysis TEXT DEFAULT '',
    recommendations TEXT DEFAULT '',
    factors TEXT DEFAULT ''
)
    """)
    conn.commit()


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_tables_exist_on_connection(conn)
    return conn


def column_exists(conn, table_name, column_name):
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(col["name"] == column_name for col in columns)


def ensure_column_exists(conn, table_name, column_name, column_type):
    if not column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        conn.commit()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            matric_number TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            created_at TEXT NOT NULL
        )
    """)
    ensure_column_exists(conn, "users", "username", "TEXT DEFAULT ''")
    ensure_column_exists(conn, "users", "phone", "TEXT DEFAULT ''")
    ensure_column_exists(conn, "users", "profile_image", "TEXT DEFAULT ''")
    ensure_column_exists(conn, "assessments", "quiz_score", "INTEGER DEFAULT 0")
    ensure_column_exists(conn, "assessments", "quiz_analysis", "TEXT DEFAULT ''")
    ensure_column_exists(conn, "assessments", "recommendations", "TEXT DEFAULT ''")
    ensure_column_exists(conn, "assessments", "factors", "TEXT DEFAULT ''")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            full_name TEXT NOT NULL,
            matric_number TEXT NOT NULL,
            department TEXT NOT NULL,
            level TEXT NOT NULL,
            course_code TEXT NOT NULL,
            course_title TEXT NOT NULL,
            study_hours INTEGER NOT NULL,
            failures INTEGER NOT NULL,
            missed_lectures INTEGER NOT NULL,
            g1 INTEGER NOT NULL,
            g2 INTEGER NOT NULL,
            confidence_level TEXT NOT NULL,
            revision_status TEXT NOT NULL,
            past_questions_practice TEXT NOT NULL,
            topic_understanding TEXT NOT NULL,
            prediction_text TEXT NOT NULL,
            confidence_score TEXT NOT NULL,
            interpretation TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    ensure_column_exists(conn, "assessments", "pretest_score", "INTEGER DEFAULT 0")
    ensure_column_exists(conn, "assessments", "pretest_level", "TEXT DEFAULT 'Low'")
    ensure_column_exists(conn, "assessments", "overall_insight", "TEXT DEFAULT ''")
    ensure_column_exists(conn, "assessments", "user_id", "INTEGER")

    conn.commit()

    # Seed default admin if not exists
    admin_email = "admin@srp.local"
    existing_admin = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (admin_email,)
    ).fetchone()

    if existing_admin is None:
        conn.execute("""
            INSERT INTO users (full_name, email, matric_number, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "System Admin",
            admin_email,
            "ADMIN-0001",
            generate_password_hash("Admin123!"),
            "admin",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()

    conn.close()


# ----------------------------
# Auth Helpers
# ----------------------------

def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view


def is_admin(user):
    if user is None:
        return False
    return str(user["role"]).strip().lower() == "admin"


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


# ----------------------------
# Assessment Helpers
# ----------------------------

def save_assessment(
    user_id,
    student_info,
    academic_input,
    readiness_answers,
    prediction_text,
    confidence_score,
    interpretation,
    pretest_score,
    pretest_level,
    overall_insight,
    quiz_score=None,
    quiz_analysis=None,
    recommendations=None,
    factors=None
):

    conn = get_db_connection()
    cursor = conn.cursor()

    safe_user_id = user_id if user_id is not None else 0

    # SAFELY CONVERT DATA TYPES

    safe_quiz_analysis = str(quiz_analysis) if quiz_analysis else ""

    safe_recommendations = json.dumps(
        recommendations
    ) if recommendations else ""

    safe_factors = json.dumps(
        factors
    ) if factors else ""

    cursor.execute("""
        INSERT INTO assessments (

            user_id,
            full_name,
            matric_number,
            department,
            level,
            course_code,
            course_title,

            study_hours,
            failures,
            missed_lectures,
            g1,
            g2,

            confidence_level,
            revision_status,
            past_questions_practice,
            topic_understanding,

            prediction_text,
            confidence_score,
            interpretation,
            created_at,

            pretest_score,
            pretest_level,
            overall_insight,

            quiz_score,
            quiz_analysis,
            recommendations,
            factors

        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?
        )

    """, (

        safe_user_id,

        student_info["full_name"],
        student_info["matric_number"],
        student_info["department"],
        student_info["level"],
        student_info["course_code"],
        student_info["course_title"],

        academic_input["study_hours"],
        academic_input["failures"],
        academic_input["missed_lectures"],
        academic_input["G1"],
        academic_input["G2"],

        readiness_answers["confidence"],
        readiness_answers["revision"],
        readiness_answers["past_questions"],
        readiness_answers["understanding"],

        prediction_text,
        str(confidence_score),
        interpretation,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        pretest_score,
        pretest_level,
        overall_insight,

        quiz_score,
        safe_quiz_analysis,
        safe_recommendations,
        safe_factors

    ))

    assessment_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return assessment_id


def get_dashboard_stats(current_user=None):

    conn = get_db_connection()

    user_id = session.get("user_id")

    # ----------------------------
    # ADMIN CAN SEE EVERYTHING
    # ----------------------------

    if current_user and is_admin(current_user):

        total_assessments = conn.execute("""
            SELECT COUNT(*) AS count
            FROM assessments
        """).fetchone()["count"]

        total_ready = conn.execute("""
            SELECT COUNT(*) AS count
            FROM assessments
            WHERE LOWER(prediction_text) = 'ready'
        """).fetchone()["count"]

        total_at_risk = conn.execute("""
            SELECT COUNT(*) AS count
            FROM assessments
            WHERE LOWER(prediction_text) = 'at risk'
        """).fetchone()["count"]

        latest_record = conn.execute("""
            SELECT full_name, prediction_text, created_at
            FROM assessments
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

    # ----------------------------
    # STUDENTS SEE ONLY THEIR OWN
    # ----------------------------

    else:

        total_assessments = conn.execute("""
            SELECT COUNT(*) AS count
            FROM assessments
            WHERE user_id = ?
        """, (user_id,)).fetchone()["count"]

        total_ready = conn.execute("""
            SELECT COUNT(*) AS count
            FROM assessments
            WHERE user_id = ?
            AND LOWER(prediction_text) = 'ready'
        """, (user_id,)).fetchone()["count"]

        total_at_risk = conn.execute("""
            SELECT COUNT(*) AS count
            FROM assessments
            WHERE user_id = ?
            AND LOWER(prediction_text) = 'at risk'
        """, (user_id,)).fetchone()["count"]

        latest_record = conn.execute("""
            SELECT full_name, prediction_text, created_at
            FROM assessments
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,)).fetchone()

    conn.close()

    return {
        "total_assessments": total_assessments,
        "total_ready": total_ready,
        "total_at_risk": total_at_risk,
        "latest_record": latest_record
    }

# ----------------------------
# Model / Scoring Helpers
# ----------------------------

def validate_range(value, min_val, max_val, field_name):
    if value < min_val or value > max_val:
        raise ValueError(f"{field_name} must be between {min_val} and {max_val}.")


def convert_study_hours_to_model_scale(study_hours):
    if study_hours <= 2:
        return 1
    elif study_hours <= 5:
        return 2
    elif study_hours <= 10:
        return 3
    else:
        return 4


def convert_failures_to_model_scale(failures):
    if failures <= 0:
        return 0
    elif failures == 1:
        return 1
    elif failures == 2:
        return 2
    return 3


def convert_missed_lectures_to_model_absences(missed_lectures):
    return round((missed_lectures / 10) * 75)


def score_pretest(answers):
    answer_key = {
        "q1": "b",
        "q2": "c",
        "q3": "a",
        "q4": "b",
        "q5": "d"
    }
    score = 0
    for key, correct_answer in answer_key.items():
        if answers.get(key) == correct_answer:
            score += 1
    return score


def get_pretest_level(score):
    if score >= 4:
        return "High"
    elif score >= 2:
        return "Moderate"
    return "Low"


def generate_overall_insight(prediction_text, pretest_level):
    if prediction_text == "Ready" and pretest_level == "High":
        return "The model and pre-test both suggest strong readiness for the examination."
    elif prediction_text == "Ready" and pretest_level == "Moderate":
        return "The model predicts readiness, but targeted revision would still improve performance."
    elif prediction_text == "Ready" and pretest_level == "Low":
        return "The model predicts readiness, but the low pre-test score suggests weak topic recall."
    elif prediction_text == "At Risk" and pretest_level == "High":
        return "The model predicts risk, but the pre-test suggests the student can recover with focused revision."
    elif prediction_text == "At Risk" and pretest_level == "Moderate":
        return "Both the model and pre-test suggest the student needs more structured preparation."
    return "The model and pre-test both suggest the student is underprepared and needs deeper revision."


def generate_recommendations(
    study_hours,
    failures,
    missed_lectures,
    g1,
    g2,
    readiness_answers,
    pretest_score
):
    recommendations = []

    if pretest_score >= 70:
        recommendations.append(
            "Maintain your current study pattern and continue revising consistently before the examination."
        )
    elif pretest_score >= 40:
        recommendations.append(
            "Focus more on weaker topics identified during the quiz assessment."
        )
    else:
        recommendations.append(
            "Revise the course material thoroughly and practice more past questions before the examination."
        )

    if study_hours < 5:
        recommendations.append(
            "Increase weekly study hours for stronger preparation."
        )

    if missed_lectures > 3:
        recommendations.append(
            "Attend lectures more consistently to improve topic understanding."
        )

    if failures > 0:
        recommendations.append(
            "Pay closer attention to previously difficult topics and seek academic support where necessary."
        )

    return recommendations


def get_prediction_factors(
    study_hours,
    failures,
    missed_lectures,
    g1,
    g2,
    pretest_score
):
    factors = []

    if pretest_score >= 70:
        factors.append(
            "Strong quiz performance demonstrates good understanding of the course material."
        )
    elif pretest_score >= 40:
        factors.append(
            "Moderate quiz performance suggests partial understanding of the course material."
        )
    else:
        factors.append(
            "Low quiz performance suggests gaps in understanding of the course material."
        )

    if study_hours >= 8:
        factors.append(
            "Consistent study hours positively contributed to the readiness prediction."
        )

    if failures == 0:
        factors.append(
            "No previous academic failures improved the readiness assessment."
        )

    if missed_lectures <= 2:
        factors.append(
            "Strong lecture attendance supported the prediction outcome."
        )

    return factors


def predict_readiness(
    student_info,
    academic_input,
    readiness_answers,
    quiz_score
):

    study_hours = int(academic_input.get("study_hours", 0))
    failures = int(academic_input.get("failures", 0))
    missed_lectures = int(academic_input.get("missed_lectures", 0))
    g1 = int(academic_input.get("G1", 0))
    g2 = int(academic_input.get("G2", 0))

    confidence = readiness_answers.get(
        "confidence",
        ""
    ).strip().lower()

    revision = readiness_answers.get(
        "revision",
        ""
    ).strip().lower()

    past_questions = readiness_answers.get(
        "past_questions",
        ""
    ).strip().lower()

    understanding = readiness_answers.get(
        "understanding",
        ""
    ).strip().lower()

    # -----------------------------------
    # SAFE QUIZ SCORE
    # -----------------------------------

    try:
        pretest_score = int(float(quiz_score))
    except:
        pretest_score = 0

    # -----------------------------------
    # CONVERT ANSWERS TO NUMBERS
    # -----------------------------------

    confidence_map = {
        "low": 0,
        "moderate": 1,
        "medium": 1,
        "high": 2
    }

    revision_map = {
        "no": 0,
        "yes": 1
    }

    past_questions_map = {
        "no": 0,
        "yes": 1
    }

    understanding_map = {
        "poor": 0,
        "fair": 1,
        "low": 0,
        "medium": 1,
        "good": 2,
        "high": 3,
        "strong": 3
    }

    # -----------------------------------
    # RAW FEATURES
    # -----------------------------------

    raw_features = {

        "studytime":
        convert_study_hours_to_model_scale(study_hours),

        "failures":
        convert_failures_to_model_scale(failures),

        "absences":
        convert_missed_lectures_to_model_absences(
            missed_lectures
        ),

        "G1": g1,

        "G2": g2,

        "confidence_score":
        confidence_map.get(confidence, 0),

        "revision_score":
        revision_map.get(revision, 0),

        "past_questions_score":
        past_questions_map.get(past_questions, 0),

        "understanding_score":
        understanding_map.get(understanding, 0),

        "pretest_score":
        pretest_score
    }

    # -----------------------------------
    # MODEL INPUT ALIGNMENT
    # -----------------------------------

    if hasattr(model, "feature_names_in_"):

        expected_columns = list(model.feature_names_in_)

        aligned_features = {}

        for col in expected_columns:
            aligned_features[col] = raw_features.get(col, 0)

        model_input = pd.DataFrame(
            [aligned_features],
            columns=expected_columns
        )

    else:

        model_input = pd.DataFrame([raw_features])

    # -----------------------------------
    # MACHINE LEARNING PREDICTION
    # -----------------------------------

    prediction = model.predict(model_input)[0]

    if hasattr(model, "predict_proba"):

        probabilities = model.predict_proba(
            model_input
        )[0]

        base_confidence = round(
            float(max(probabilities)) * 100
        )

    else:

        base_confidence = 70

    # -----------------------------------
    # HYBRID SCORING SYSTEM
    # -----------------------------------

    positive_score = 0

    # QUIZ PERFORMANCE

    if pretest_score >= 85:
        positive_score += 4

    elif pretest_score >= 70:
        positive_score += 3

    elif pretest_score >= 50:
        positive_score += 2

    elif pretest_score >= 40:
        positive_score += 1

    # TEST SCORES

    if g1 >= 15:
        positive_score += 1

    if g2 >= 15:
        positive_score += 1

    # STUDY HOURS

    if study_hours >= 8:
        positive_score += 2

    elif study_hours >= 5:
        positive_score += 1

    # FAILURES

    if failures == 0:
        positive_score += 1

    # ATTENDANCE

    if missed_lectures <= 2:
        positive_score += 1

    # CONFIDENCE

    if confidence == "high":
        positive_score += 1

    elif confidence in ["moderate", "medium"]:
        positive_score += 0.5

    # REVISION

    if revision == "yes":
        positive_score += 1

    # PAST QUESTIONS

    if past_questions == "yes":
        positive_score += 1

    # UNDERSTANDING

    if understanding in ["good", "strong", "high"]:
        positive_score += 1

    # -----------------------------------
    # FINAL PREDICTION
    # -----------------------------------

    if positive_score >= 9:

        prediction_text = "Ready"

    elif positive_score >= 6:

        prediction_text = "Ready"

    else:

        prediction_text = "At Risk"

    # -----------------------------------
    # REALISTIC CONFIDENCE SCORE
    # -----------------------------------

    confidence_value = 50

    if prediction_text == "Ready":
        confidence_value += 15

    if pretest_score >= 85:
        confidence_value += 15

    elif pretest_score >= 70:
        confidence_value += 10

    elif pretest_score >= 50:
        confidence_value += 5

    if g1 >= 15:
        confidence_value += 5

    if g2 >= 15:
        confidence_value += 5

    if study_hours >= 8:
        confidence_value += 5

    if failures == 0:
        confidence_value += 3

    if understanding in ["good", "strong", "high"]:
        confidence_value += 5

    # Prevent fake 100%

    confidence_value = min(confidence_value, 98)

    # Prevent too low

    confidence_value = max(confidence_value, 35)

    confidence_score = f"{round(confidence_value)}%"

    # -----------------------------------
    # INTERPRETATION
    # -----------------------------------

    if prediction_text == "Ready":

        interpretation = (
            "The student appears academically "
            "prepared for the examination. "
            "Academic records, readiness "
            "responses, and quiz performance "
            "indicate a strong likelihood "
            "of examination readiness."
        )

    else:

        interpretation = (
            "The student may require additional "
            "academic preparation before the "
            "examination. Current indicators "
            "suggest possible readiness gaps."
        )

    # -----------------------------------
    # PRETEST LEVEL
    # -----------------------------------

    if pretest_score >= 70:

        pretest_level = "High"

    elif pretest_score >= 40:

        pretest_level = "Moderate"

    else:

        pretest_level = "Low"

    # -----------------------------------
    # OVERALL INSIGHT
    # -----------------------------------

    if pretest_score >= 70:

        overall_insight = (
            "Quiz performance demonstrates "
            "strong understanding of the "
            "course material."
        )

    elif pretest_score >= 40:

        overall_insight = (
            "The student shows moderate "
            "understanding but still has "
            "areas requiring improvement."
        )

    else:

        overall_insight = (
            "Quiz performance suggests "
            "weak understanding of major "
            "course concepts."
        )

    # -----------------------------------
    # RECOMMENDATIONS
    # -----------------------------------

    recommendations = []

    if pretest_score < 50:

        recommendations.append(
            "Revise the course material "
            "more thoroughly before the examination."
        )

    if study_hours < 5:

        recommendations.append(
            "Increase weekly study hours "
            "for better preparation."
        )

    if missed_lectures > 3:

        recommendations.append(
            "Improve lecture attendance "
            "and classroom participation."
        )

    if past_questions == "no":

        recommendations.append(
            "Practice more past questions "
            "to improve examination confidence."
        )

    if not recommendations:

        recommendations.append(
            "Maintain your current preparation "
            "strategy and continue revising consistently."
        )

    # -----------------------------------
    # FACTORS
    # -----------------------------------

    factors = []

    if pretest_score >= 70:

        factors.append(
            "Strong quiz performance positively "
            "influenced the prediction."
        )

    else:

        factors.append(
            "Low quiz performance reduced "
            "overall readiness confidence."
        )

    if study_hours >= 5:

        factors.append(
            "Consistent study hours positively "
            "contributed to readiness."
        )

    if failures == 0:

        factors.append(
            "No previous academic failures "
            "improved the assessment."
        )

    if missed_lectures <= 2:

        factors.append(
            "Strong lecture attendance supported "
            "the prediction outcome."
        )

    # -----------------------------------
    # RETURN RESULTS
    # -----------------------------------

    return {

        "prediction_text": prediction_text,

        "confidence_score": confidence_score,

        "interpretation": interpretation,

        "pretest_score": pretest_score,

        "pretest_level": pretest_level,

        "overall_insight": overall_insight,

        "recommendations": recommendations,

        "factors": factors
    }
# ----------------------------
# Routes
# ----------------------------


@app.route("/")
@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    current_user = get_current_user()

    stats = get_dashboard_stats(current_user)

    return render_template("home.html", stats=stats)

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        conn = get_db_connection()

        # LOGIN USING EMAIL OR MATRIC NUMBER
        user = conn.execute("""
            SELECT * FROM users
            WHERE email = ?
            OR matric_number = ?
        """, (email, email)).fetchone()

        conn.close()

        if user and check_password_hash(user["password_hash"], password):

            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]

            return redirect(url_for("dashboard"))

        return render_template(
            "login.html",
            error="Invalid login credentials."
        )

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        full_name = request.form.get("full_name")
        email = request.form.get("email")
        matric_number = request.form.get("matric_number")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        # VALIDATION
        if password != confirm_password:
            return render_template(
                "register.html",
                error="Passwords do not match."
            )

        conn = get_db_connection()

        # CHECK IF EMAIL EXISTS
        existing_email = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing_email:
            conn.close()

            return render_template(
                "register.html",
                error="Email already exists."
            )

        # CHECK IF MATRIC NUMBER EXISTS
        existing_matric = conn.execute(
            "SELECT * FROM users WHERE matric_number = ?",
            (matric_number,)
        ).fetchone()

        if existing_matric:
            conn.close()

            return render_template(
                "register.html",
                error="Matric number already exists."
            )

        # HASH PASSWORD
        password_hash = generate_password_hash(password)

        # INSERT USER
        conn.execute("""
            INSERT INTO users (
                full_name,
                email,
                matric_number,
                password_hash,
                role,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            full_name,
            email,
            matric_number,
            password_hash,
            "student",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        conn.commit()

        # GET NEW USER
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        # CREATE SESSION
        session["user_id"] = user["id"]
        session["user_email"] = user["email"]
        session["full_name"] = user["full_name"]
        session["role"] = user["role"]

        return redirect(url_for("dashboard"))

    return render_template("register.html")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    current_user = get_current_user()

    if not current_user:
        return redirect(url_for("login"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        matric_number = request.form.get("matric_number", "").strip()
        username = request.form.get("username", "").strip()
        phone = request.form.get("phone", "").strip()

        conn = get_db_connection()

        profile_image = current_user["profile_image"] if "profile_image" in current_user.keys() else ""

        image = request.files.get("profile_image")

        if image and image.filename:
            allowed_images = {"png", "jpg", "jpeg", "webp"}

            ext = image.filename.rsplit(".", 1)[1].lower()

            if ext in allowed_images:
                profile_folder = os.path.join(app.static_folder, "profile_pics")
                os.makedirs(profile_folder, exist_ok=True)

                filename = secure_filename(f"user_{current_user['id']}.{ext}")
                image_path = os.path.join(profile_folder, filename)

                image.save(image_path)

                profile_image = f"profile_pics/{filename}"

        conn.execute("""
            UPDATE users
            SET full_name = ?,
                email = ?,
                matric_number = ?,
                username = ?,
                phone = ?,
                profile_image = ?
            WHERE id = ?
        """, (
            full_name,
            email,
            matric_number,
            username,
            phone,
            profile_image,
            current_user["id"]
        ))

        conn.execute("""
            UPDATE assessments
            SET full_name = ?,
                matric_number = ?
            WHERE user_id = ?
        """, (
            full_name,
            matric_number,
            current_user["id"]
        ))

        conn.commit()
        conn.close()

        session["full_name"] = full_name
        session["user_email"] = email

        return redirect(url_for("profile"))

    return render_template("profile.html", user=current_user)

@app.route("/history")
def history():
    query = (
        request.args.get("query")
        or request.args.get("search")
        or request.args.get("q")
        or ""
    ).strip()

    prediction_filter = request.args.get("prediction_filter", "").strip()

    conn = get_db_connection()
    current_user = get_current_user()

    sql = """
        SELECT
            id,
            full_name,
            matric_number,
            course_code,
            prediction_text,
            confidence_score,
            created_at
        FROM assessments
        WHERE 1=1
    """

    params = []

    if current_user and not is_admin(current_user):
        sql += " AND user_id = ?"
        params.append(session.get("user_id"))

    if query:
        sql += """
            AND (
                LOWER(full_name) LIKE LOWER(?)
                OR LOWER(matric_number) LIKE LOWER(?)
                OR LOWER(course_code) LIKE LOWER(?)
                OR LOWER(course_title) LIKE LOWER(?)
            )
        """
        search_value = f"%{query}%"
        params.extend([search_value, search_value, search_value, search_value])

    if prediction_filter:
        sql += " AND prediction_text = ?"
        params.append(prediction_filter)

    sql += " ORDER BY id DESC"

    assessments = conn.execute(sql, params).fetchall()
    conn.close()

    return render_template(
        "history.html",
        assessments=assessments,
        query=query,
        prediction_filter=prediction_filter
    )

@app.route("/delete_assessment/<int:assessment_id>", methods=["POST"])
def delete_assessment(assessment_id):

    current_user = get_current_user()

    conn = get_db_connection()

    # ADMIN CAN DELETE ANYTHING

    if current_user and is_admin(current_user):

        conn.execute(
            "DELETE FROM assessments WHERE id = ?",
            (assessment_id,)
        )

    # STUDENTS CAN DELETE ONLY THEIR OWN

    else:

        conn.execute(
            """
            DELETE FROM assessments
            WHERE id = ?
            AND user_id = ?
            """,
            (
                assessment_id,
                session.get("user_id")
            )
        )

    conn.commit()
    conn.close()

    return redirect(url_for("history"))

@app.route("/assessment_detail/<int:assessment_id>")
def assessment_detail(assessment_id):

    current_user = get_current_user()

    conn = get_db_connection()

    # ----------------------------
    # ADMIN CAN VIEW EVERYTHING
    # ----------------------------

    if current_user and is_admin(current_user):

        assessment = conn.execute("""
            SELECT *
            FROM assessments
            WHERE id = ?
        """, (assessment_id,)).fetchone()

    # ----------------------------
    # STUDENTS CAN VIEW ONLY THEIRS
    # ----------------------------

    else:

        assessment = conn.execute("""
            SELECT *
            FROM assessments
            WHERE id = ?
            AND user_id = ?
        """, (
            assessment_id,
            session.get("user_id")
        )).fetchone()

    conn.close()

    # ----------------------------
    # IF NO RECORD FOUND
    # ----------------------------

    if not assessment:
        return redirect(url_for("history"))

    confidence_gap_message = None

    # ----------------------------
    # SAFE QUIZ SCORE
    # ----------------------------

    try:
        quiz_score = int(float(assessment["quiz_score"] or 0))
    except:
        quiz_score = 0

    confidence_level = str(
        assessment["confidence_level"]
    ).strip().lower()

    # ----------------------------
    # CONFIDENCE GAP ANALYSIS
    # ----------------------------

    if (
        confidence_level == "high"
        and quiz_score < 40
    ):

        confidence_gap_message = (
            "The student reported high confidence, but actual quiz "
            "performance was poor. This suggests a mismatch between "
            "perceived readiness and actual understanding."
        )

    elif (
        confidence_level in ["low", "moderate"]
        and quiz_score >= 70
    ):

        confidence_gap_message = (
            "The student performed better in the quiz than their "
            "self-reported confidence initially suggested."
        )

    return render_template(
        "assessment_detail.html",
        assessment=assessment,
        confidence_gap_message=confidence_gap_message
    )
# =========================
# UPLOAD PAGE
# =========================
@app.route("/upload_material", methods=["GET", "POST"])
def upload_material():
    mode = request.args.get("mode", "assessment")
    session["quiz_mode"] = mode

    if request.method == "POST":
        if "file" not in request.files:
            return render_template(
                "upload_material.html",
                mode=mode,
                error="No file was selected."
            )

        file = request.files["file"]

        if file.filename == "":
            return render_template(
                "upload_material.html",
                mode=mode,
                error="Please choose a file before submitting."
            )

        if not allowed_file(file.filename):
            return render_template(
                "upload_material.html",
                mode=mode,
                error="Unsupported file format. Please upload PDF, DOCX, or TXT."
            )

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        extracted_text = extract_text_from_file(filepath)

        if not extracted_text or not extracted_text.strip():
            return render_template(
                "upload_material.html",
                mode=mode,
                error="Could not extract readable text from the uploaded file."
            )

        stored_text_file = store_quiz_content(extracted_text)

        if not stored_text_file:
            return render_template(
                "upload_material.html",
                mode=mode,
                error="Could not store extracted content for quiz generation."
            )

        session["content_file"] = stored_text_file
        session["uploaded_filename"] = filename

        return redirect(url_for("generate_quiz"))

    return render_template("upload_material.html", mode=mode)

@app.route("/student_info", methods=["GET", "POST"])
def student_info():
    if request.method == "POST":
        student_data = {
            "full_name": request.form.get("full_name", "").strip(),
            "matric_number": request.form.get("matric_number", "").strip(),
            "department": request.form.get("department", "").strip(),
            "level": request.form.get("level", "").strip(),
            "course_code": request.form.get("course_code", "").strip(),
            "course_title": request.form.get("course_title", "").strip()
        }

        session["student_info"] = student_data
        return redirect(url_for("academic_input"))

    initial_data = session.get("student_info", {
        "full_name": "",
        "matric_number": "",
        "department": "",
        "level": "",
        "course_code": "",
        "course_title": ""
    })

    return render_template("student_info.html", initial_data=initial_data)


@app.route("/academic-input", methods=["GET", "POST"])
@app.route("/academic_input", methods=["GET", "POST"])
def academic_input():
    if request.method == "POST":
        session["academic_input"] = {
            "study_hours": int(request.form.get("study_hours", 0) or 0),
            "failures": int(request.form.get("failures", 0) or 0),
            "missed_lectures": int(request.form.get("missed_lectures", 0) or 0),
            "G1": int(request.form.get("G1", 0) or 0),
            "G2": int(request.form.get("G2", 0) or 0),
        }
        return redirect(url_for("readiness_questions"))
    return render_template("academic_input.html")


@app.route("/readiness-questions", methods=["GET", "POST"])
@app.route("/readiness_questions", methods=["GET", "POST"])
def readiness_questions():
    if request.method == "POST":
        session["readiness_answers"] = {
            "confidence": request.form.get("confidence", "").strip().lower(),
            "revision": request.form.get("revision", "").strip().lower(),
            "past_questions": request.form.get("past_questions", "").strip().lower(),
            "understanding": request.form.get("understanding", "").strip().lower(),
        }
        return redirect(url_for("upload_material"))
    return render_template("readiness_questions.html")

# =========================
# GENERATE QUIZ
# =========================
@app.route("/generate_quiz")
def generate_quiz():
    mode = session.get("quiz_mode", "assessment")
    content = get_stored_quiz_content()

    if not content or not content.strip():
        return redirect(url_for("upload_material", mode=mode))

    questions = generate_questions_from_text(content, num_questions=10)

    if not questions:
        return render_template(
            "generated_quiz.html",
            questions=[],
            error="Could not generate quiz questions from the uploaded material."
        )

    session["questions"] = questions

    return render_template(
        "generated_quiz.html",
        questions=questions,
        mode=mode
    )

# =========================
# SUBMIT QUIZ
# =========================
@app.route("/submit_generated_quiz", methods=["POST"])
def submit_generated_quiz():

    questions = session.get("questions", [])
    content = get_stored_quiz_content()
    mode = session.get("quiz_mode", "assessment")

    if not questions:
        return redirect(url_for("upload_material", mode=mode))

    if not content or not content.strip():
        return redirect(url_for("upload_material", mode=mode))

    student_answers = []

    for i in range(len(questions)):
        answer = request.form.get(f"answer_{i}", "").strip()
        student_answers.append(answer)

    graded_results = []
    score = 0

    analysis = "Performance analysis is currently unavailable."

    grading_response = grade_all_answers_with_gemini(
        questions=questions,
        student_answers=student_answers,
        course_material=content
    )

    parsed = extract_json_payload(grading_response)

    # -----------------------------------
    # SUCCESSFUL GRADING
    # -----------------------------------

    if parsed and "results" in parsed and isinstance(parsed["results"], list):

        total_score = 0

        for i, item in enumerate(parsed["results"]):

            question_text = (
                questions[i]
                if i < len(questions)
                else f"Question {i+1}"
            )

            student_answer = (
                student_answers[i]
                if i < len(student_answers)
                else ""
            )

            try:
                item_score = int(item.get("score", 0))
            except Exception:
                item_score = 0

            total_score += item_score

            graded_results.append({
                "question": question_text,
                "student_answer": student_answer,
                "expected_answer": item.get(
                    "expected_answer",
                    "No expected answer generated."
                ),
                "score": item_score,
                "verdict": item.get(
                    "verdict",
                    "Ungraded"
                ),
                "feedback": item.get(
                    "feedback",
                    "No feedback provided."
                )
            })

        if graded_results:

            score = round(
                (total_score / (len(graded_results) * 10)) * 100
            )

        analysis = analyze_performance_with_gemini(
            graded_results,
            score
        )

    # -----------------------------------
    # FAILED GRADING
    # -----------------------------------

    else:

        for i, q in enumerate(questions):

            graded_results.append({
                "question": q,
                "student_answer": (
                    student_answers[i]
                    if i < len(student_answers)
                    else ""
                ),
                "expected_answer": (
                    "No expected answer generated."
                ),
                "score": 0,
                "verdict": "Ungraded",
                "feedback": (
                    "The grading response could not "
                    "be processed correctly."
                )
            })

        score = 0

        analysis = (
            "The system could not properly "
            "grade the quiz responses."
        )

    # -----------------------------------
    # SAVE SESSION
    # -----------------------------------

    session["quiz_score"] = float(score)
    session["quiz_analysis"] = analysis

    session.modified = True

    print("QUIZ SCORE SAVED:", session.get("quiz_score"))

    # -----------------------------------
    # RENDER RESULT
    # -----------------------------------
    print("SESSION QUIZ SCORE AFTER SAVE:", session.get("quiz_score"))
    print("SESSION DATA:", dict(session))

    return render_template(
        "quiz_result.html",
        score=score,
        analysis=analysis,
        graded_results=graded_results,
        practice_mode=(mode == "practice")
    )

@app.route("/final_prediction")
def final_prediction():

    student_info = session.get("student_info")
    academic_input = session.get("academic_input")
    readiness_answers = session.get("readiness_answers")
    quiz_score = session.get("quiz_score", 0)

    print("QUIZ SCORE INSIDE FINAL PREDICTION:", quiz_score)
    print("FULL SESSION INSIDE FINAL PREDICTION:", dict(session))

    # SAFELY HANDLE QUIZ SCORE

    print("QUIZ SCORE RETRIEVED:", quiz_score)

    if not student_info:
        return redirect(url_for("student_info"))

    if not academic_input:
        return redirect(url_for("academic_input"))

    if not readiness_answers:
        return redirect(url_for("readiness_questions"))

    # GENERATE PREDICTION
    result = predict_readiness(
        student_info=student_info,
        academic_input=academic_input,
        readiness_answers=readiness_answers,
        quiz_score=quiz_score
    )


    # -----------------------------------
    # SAVE ASSESSMENT
    # -----------------------------------

    assessment_id = save_assessment(

    user_id=session.get("user_id") or 0,

    student_info=student_info,

    academic_input=academic_input,

    readiness_answers=readiness_answers,

    prediction_text=result["prediction_text"],

    confidence_score=result["confidence_score"],

    interpretation=result["interpretation"],

    pretest_score=result["pretest_score"],

    pretest_level=result["pretest_level"],

    overall_insight=result["overall_insight"],

    quiz_score=quiz_score,

    quiz_analysis=session.get("quiz_analysis"),

    recommendations=result.get("recommendations"),

    factors=result.get("factors")
)

    session["latest_assessment_id"] = assessment_id

    # -----------------------------------
    # CONFIDENCE GAP ANALYSIS
    # -----------------------------------

    confidence_gap_message = None

    confidence_level = readiness_answers.get(
        "confidence",
        ""
    ).strip().lower()

    if confidence_level == "high" and quiz_score < 40:

        confidence_gap_message = (
            "The student reported high confidence, but actual quiz "
            "performance was poor. This suggests a mismatch between "
            "perceived readiness and actual understanding."
        )

    elif confidence_level in ["low", "moderate"] and quiz_score >= 70:

        confidence_gap_message = (
            "The student performed better in the quiz than their "
            "self-reported confidence initially suggested."
        )

    # -----------------------------------
    # RENDER RESULT PAGE
    # -----------------------------------

    return render_template(
        "result.html",

        student_info=student_info,

        academic_inputs={
            "studytime": academic_input.get("study_hours"),
            "failures": academic_input.get("failures"),
            "absences": academic_input.get("missed_lectures"),
            "g1": academic_input.get("G1"),
            "g2": academic_input.get("G2"),
        },

        readiness_answers={
            "confidence": readiness_answers.get("confidence"),
            "revision": readiness_answers.get("revision"),
            "past_questions": readiness_answers.get("past_questions"),
            "understanding": readiness_answers.get("understanding"),
        },

        prediction_text=result["prediction_text"],
        confidence_score=result["confidence_score"],
        interpretation=result["interpretation"],

        pretest_score=result["pretest_score"],
        pretest_level=result["pretest_level"],

        overall_insight=result["overall_insight"],

        recommendations=result["recommendations"],
        factors=result["factors"],

        quiz_score=quiz_score,

        quiz_analysis=session.get("quiz_analysis"),

        confidence_gap_message=confidence_gap_message
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))

@app.route("/export_history")
def export_history():
    current_user = get_current_user()
    conn = get_db_connection()

    query = request.args.get("q", "").strip()
    prediction_filter = request.args.get("prediction", "").strip()

    sql = """
        SELECT id, full_name, matric_number, department, level, course_code, course_title,
               study_hours, failures, missed_lectures, g1, g2,
               confidence_level, revision_status, past_questions_practice, topic_understanding,
               prediction_text, confidence_score, interpretation,
               pretest_score, pretest_level, overall_insight, created_at
        FROM assessments
        WHERE 1=1
    """
    params = []

    if current_user and not is_admin(current_user):
        user_id = session.get("user_id")
        sql += " AND user_id = ?"
        params.append(user_id)

    if query:
        sql += " AND (full_name LIKE ? OR matric_number LIKE ? OR course_code LIKE ?)"
        like_query = f"%{query}%"
        params.extend([like_query, like_query, like_query])

    if prediction_filter:
        sql += " AND prediction_text = ?"
        params.append(prediction_filter)

    sql += " ORDER BY id DESC"

    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "ID",
        "Full Name",
        "Matric Number",
        "Department",
        "Level",
        "Course Code",
        "Course Title",
        "Study Hours",
        "Failures",
        "Missed Lectures",
        "G1",
        "G2",
        "Confidence Level",
        "Revision Status",
        "Past Questions Practice",
        "Topic Understanding",
        "Prediction",
        "Confidence Score",
        "Interpretation",
        "Pretest Score",
        "Pretest Level",
        "Overall Insight",
        "Created At"
    ])

    for row in rows:
        writer.writerow([
            row["id"],
            row["full_name"],
            row["matric_number"],
            row["department"],
            row["level"],
            row["course_code"],
            row["course_title"],
            row["study_hours"],
            row["failures"],
            row["missed_lectures"],
            row["g1"],
            row["g2"],
            row["confidence_level"],
            row["revision_status"],
            row["past_questions_practice"],
            row["topic_understanding"],
            row["prediction_text"],
            row["confidence_score"],
            row["interpretation"],
            row["pretest_score"],
            row["pretest_level"],
            row["overall_insight"],
            row["created_at"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=prediction_history.csv"}
    )
# =========================
# RESET
# =========================
@app.route("/reset")
def reset():
    keys_to_remove = [
        "student_info",
        "academic_input",
        "readiness_answers",
        "quiz_score",
        "quiz_analysis",
        "graded_results",
        "questions",
        "content_file",
        "uploaded_filename",
        "latest_assessment_id",
        "quiz_mode"
    ]

    for key in keys_to_remove:
        session.pop(key, None)

    return redirect(url_for("dashboard"))

@app.route("/check_table")
def check_table():

    conn = get_db_connection()

    columns = conn.execute(
        "PRAGMA table_info(assessments)"
    ).fetchall()

    conn.close()

    output = []

    for col in columns:
        output.append({
            "id": col[0],
            "name": col[1],
            "type": col[2]
        })

    return str(output)

#@app.route("/upgrade_db")
#def upgrade_db():

    conn = get_db_connection()

    conn.execute(
        "ALTER TABLE assessments ADD COLUMN quiz_score INTEGER"
    )

    conn.execute(
        "ALTER TABLE assessments ADD COLUMN quiz_analysis TEXT"
    )

    conn.execute(
        "ALTER TABLE assessments ADD COLUMN recommendations TEXT"
    )

    conn.execute(
        "ALTER TABLE assessments ADD COLUMN factors TEXT"
    )

    conn.commit()

    conn.close()

    return "Database upgraded successfully"

# =========================
# RUN
# =========================
init_db()
if __name__ == "__main__":
    app.run(debug=True)