import os
import json
import re
import time
from docx import Document
from PyPDF2 import PdfReader
import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel("models/gemini-2.5-flash")


def extract_text_from_file(filepath):
    text = ""

    if filepath.endswith(".txt"):
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

    elif filepath.endswith(".pdf"):
        with open(filepath, "rb") as f:
            reader = PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ""

    elif filepath.endswith(".docx"):
        doc = Document(filepath)
        for para in doc.paragraphs:
            text += para.text + "\n"

    return text.strip()


def safe_generate_content(prompt, retries=2, delay=2):
    last_error = None

    for _ in range(retries + 1):
        try:
            response = gemini_model.generate_content(prompt)
            if hasattr(response, "text") and response.text:
                return response.text.strip()
            return ""
        except Exception as e:
            last_error = e
            error_text = str(e).lower()

            if "429" in error_text or "quota" in error_text or "resourceexhausted" in error_text:
                return ""

            time.sleep(delay)

    if last_error:
        raise last_error
    return ""


def clean_question_text(text):
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"^[-•*]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_material_into_sentences(content):
    content = re.sub(r"\s+", " ", content).strip()
    sentences = re.split(r'(?<=[.!?])\s+', content)
    return [s.strip() for s in sentences if len(s.strip()) > 35]


def build_expected_answer_from_material(question, course_material):
    sentences = split_material_into_sentences(course_material)

    if not sentences:
        return "A correct answer should be drawn directly from the uploaded material."

    question_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", question.lower()))

    ranked = []
    for sentence in sentences:
        sentence_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", sentence.lower()))
        overlap = len(question_words & sentence_words)
        ranked.append((overlap, sentence))

    ranked.sort(key=lambda x: x[0], reverse=True)

    best_sentences = [s for score, s in ranked[:3] if score > 0]

    if not best_sentences:
        best_sentences = sentences[:2]

    answer = " ".join(best_sentences[:2]).strip()
    return answer[:350] if len(answer) > 350 else answer


def fallback_generate_questions_from_text(content, num_questions=10):
    sentences = split_material_into_sentences(content)

    questions = []
    starters = [
        "Explain",
        "Discuss",
        "Describe",
        "What is the significance of",
        "How would you explain",
        "Compare",
        "Why is",
        "What conclusion can be drawn about",
        "How does",
        "State the importance of"
    ]

    cleaned_sentences = []
    for sentence in sentences:
        s = sentence.strip()
        s = re.sub(r"\*\*", "", s)
        s = re.sub(r"[•\-]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()

        bad_patterns = [
            "prof.",
            "csc",
            "course code",
            "oyelade",
            "topic to be covered",
            "introduction to artificial intelligence prof",
        ]

        if any(bp in s.lower() for bp in bad_patterns):
            continue

        if len(s) < 35:
            continue

        cleaned_sentences.append(s)

    for i, sentence in enumerate(cleaned_sentences[:num_questions * 2]):
        starter = starters[len(questions) % len(starters)]

        shortened = sentence[:120].rstrip(" .,;:")
        q = f"{starter} {shortened.lower()}."
        q = q[0].upper() + q[1:]

        q = re.sub(r"\s+", " ", q).strip()

        if len(q) > 20 and q not in questions:
            questions.append(q)

        if len(questions) == num_questions:
            break

    while len(questions) < num_questions:
        questions.append(f"Explain one important concept discussed in the material.")

    return questions[:num_questions]


def generate_questions_from_text(content, num_questions=10):
    prompt = f"""
You are an expert university lecturer.

Generate exactly {num_questions} short-answer questions from the uploaded course material.

STRICT RULES:
1. Write clear, intelligent, natural exam questions.
2. Questions must test understanding, explanation, application, comparison, or reasoning.
3. Do NOT copy raw sentences directly from the material.
4. Do NOT include bullet points.
5. Do NOT include asterisks.
6. Do NOT include symbols like • or - in the questions.
7. Do NOT say "based on the material".
8. Do NOT mention lecturer names, course codes, page headers, or titles as the answer focus.
9. Keep each question concise and meaningful.
10. Return only a numbered list from 1 to {num_questions}.

Course Material:
{content[:9000]}
"""

    response_text = safe_generate_content(prompt)

    questions = []

    if response_text:
        for line in response_text.splitlines():
            line = line.strip()
            if not line:
                continue

            match = re.match(r"^\d+[\).\s-]+(.+)$", line)
            if not match:
                continue

            q = match.group(1).strip()

            q = re.sub(r"\*\*", "", q)
            q = re.sub(r"[•\-]+", " ", q)
            q = re.sub(r"\s+", " ", q).strip()

            bad_patterns = [
                "prof.",
                "csc",
                "course code",
                "oyelade",
                "topic to be covered",
                "based on the material",
            ]

            if any(bp in q.lower() for bp in bad_patterns):
                continue

            if len(q) < 18:
                continue

            questions.append(q)

    if len(questions) >= num_questions:
        return questions[:num_questions]

    return fallback_generate_questions_from_text(content, num_questions)


def fallback_grade_quiz(questions, student_answers, course_material):
    graded_results = []

    weak_feedbacks = [
        "Your answer does not address the main idea of the question.",
        "This response is too vague and does not reflect the material.",
        "Your answer is not relevant to what the question is asking.",
        "The response does not show understanding of the concept tested.",
        "This answer does not connect meaningfully with the uploaded material."
    ]

    partial_feedbacks = [
        "Your answer shows slight relevance, but it is incomplete.",
        "There is some connection to the material, but key points are missing.",
        "The response has limited accuracy and needs clearer explanation.",
        "You touched on the topic, but the explanation is too shallow.",
        "Your answer contains a small correct idea, but it is not enough."
    ]

    good_feedbacks = [
        "Your answer is relevant and reflects the material reasonably well.",
        "This response addresses the question with fair accuracy.",
        "You showed a good understanding of the concept.",
        "Your answer is mostly aligned with the expected idea.",
        "This is a solid response with useful relevant points."
    ]

    for i, question in enumerate(questions):
        answer = student_answers[i].strip() if i < len(student_answers) else ""
        expected_answer = build_expected_answer_from_material(question, course_material)

        if not answer:
            graded_results.append({
                "id": i + 1,
                "expected_answer": expected_answer,
                "score": 0,
                "verdict": "No Attempt",
                "feedback": "No answer was provided for this question."
            })
            continue

        answer_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", answer.lower()))
        expected_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", expected_answer.lower()))
        overlap = len(answer_words & expected_words)

        if overlap >= 6:
            score = 7
            verdict = "Partially Correct"
            feedback = good_feedbacks[i % len(good_feedbacks)]
        elif overlap >= 3:
            score = 4
            verdict = "Weak"
            feedback = partial_feedbacks[i % len(partial_feedbacks)]
        else:
            score = 0
            verdict = "Incorrect"
            feedback = weak_feedbacks[i % len(weak_feedbacks)]

        graded_results.append({
            "id": i + 1,
            "expected_answer": expected_answer,
            "score": score,
            "verdict": verdict,
            "feedback": feedback
        })

    return {"results": graded_results}


def grade_all_answers_with_gemini(questions, student_answers, course_material):
    prompt = f"""
You are a strict but fair academic grader.

Using ONLY the uploaded course material, grade each student's answer.

For EACH question:
- produce a short expected answer drawn from the material
- give a score from 0 to 10
- give one verdict from this list only:
  Correct, Partially Correct, Weak, Incorrect, No Attempt
- give one short feedback sentence

IMPORTANT:
- Return ONLY valid JSON
- Do not add markdown
- Do not wrap the JSON in backticks
- Do not include extra commentary

Return in this exact structure:
{{
  "results": [
    {{
      "id": 1,
      "expected_answer": "Short expected answer",
      "score": 0,
      "verdict": "Incorrect",
      "feedback": "Short feedback"
    }}
  ]
}}

Questions:
{json.dumps(questions, ensure_ascii=False)}

Student Answers:
{json.dumps(student_answers, ensure_ascii=False)}

Course Material:
{course_material[:10000]}
"""

    response_text = safe_generate_content(prompt)

    if response_text:
        return response_text

    fallback = fallback_grade_quiz(questions, student_answers, course_material)
    return json.dumps(fallback, ensure_ascii=False)


def analyze_performance_with_gemini(graded_results, score):
    prompt = f"""
You are an academic performance assistant.

A student scored {score}% in a quiz.

Based on the results, return a short and simple summary in this exact format:

Understanding: ...
Weakness: ...
Pattern: ...
Recommendation: ...

Results:
{json.dumps(graded_results, ensure_ascii=False)}
"""

    response_text = safe_generate_content(prompt)

    if response_text:
        return response_text

    if score >= 70:
        return (
            "Understanding: Strong\n"
            "Weakness: Minor gaps only\n"
            "Pattern: Good grasp of the material\n"
            "Recommendation: Continue revising and practice more past questions."
        )
    elif score >= 40:
        return (
            "Understanding: Fair\n"
            "Weakness: Incomplete explanation of key concepts\n"
            "Pattern: Partial understanding\n"
            "Recommendation: Focus on weak topics and revise more consistently."
        )
    else:
        return (
            "Understanding: Poor\n"
            "Weakness: Weak understanding of major concepts\n"
            "Pattern: Low mastery of the material\n"
            "Recommendation: Re-read the material carefully and practice answering questions in your own words."
        )