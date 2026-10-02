"""The grader must pass ideal answers and fail wrong ones, or eval scores mean nothing."""
import copy

from eval.grader import load_cases
from eval.run_eval import run_case

from .conftest import FakeLLM

IDEAL = {
    "Homepage sign-off": [
        {"type": "approval", "topic": "Colour scheme", "value": "Maroon and gold", "text": "Maroon and gold colour scheme approved", "who": "Ritu Sharma", "line": 1, "quote": "The maroon and gold colour scheme is approved."},
        {"type": "approval", "topic": "Homepage banner", "value": "Diwali hampers banner", "text": "Diwali hamper banner approved", "who": "Ritu Sharma", "line": 3, "quote": "Yes, approved. Use that banner for the launch."},
        {"type": "decision", "topic": "Payment methods", "value": "Razorpay and COD", "text": "Accept Razorpay and cash on delivery", "who": "Ritu Sharma", "line": 5, "quote": "Yes, both. COD is important for us."},
        {"type": "decision", "topic": "Launch date", "value": "5 October", "text": "Launch on 5 October", "who": "Ritu Sharma", "line": 7, "quote": "5 October is perfect"},
        {"type": "action", "topic": "Product descriptions", "value": "by Friday", "text": "Ritu sends product descriptions", "owner": "Ritu Sharma", "due": "by Friday", "dueDate": "2026-09-18", "line": 9, "quote": "I'll send them by Friday."}],
    "Pricing page review": [
        {"type": "open_question", "topic": "Pricing page", "value": "Option B considered", "text": "Client considering Option B, not yet decided", "who": "Arvind", "line": 3, "quote": "Not yet. Maybe."},
        {"type": "action", "topic": "Pricing page", "value": "Third variation", "text": "Agency sends a third variation", "owner": "Neha", "due": "by Wednesday", "dueDate": "2026-09-23", "line": 4, "quote": "we'll send a third variation by Wednesday"},
        {"type": "decision", "topic": "Logo", "value": "Unchanged", "text": "Logo stays exactly as it is", "who": "Arvind", "line": 7, "quote": "Yes, the logo stays exactly as it is."}],
    "Launch planning": [
        {"type": "decision", "topic": "Launch date", "value": "27 October", "text": "Launch on 27 October", "who": "Faisal", "line": 3, "quote": "Make the launch 27 October instead."},
        {"type": "decision", "topic": "Homepage media", "value": "Photo slider", "text": "Use a photo slider instead of video", "who": "Faisal", "line": 5, "quote": "Just use a photo slider.", "replaces": ["P1"]}],
    "Launch check-in": [
        {"type": "decision", "topic": "Launch date", "value": "12 October", "text": "Launch moved to 12 October", "who": "Ritu Sharma", "line": 1, "quote": "Let's go live on 12 October.", "replaces": ["P1"]},
        {"type": "decision", "topic": "Payment methods", "value": "Razorpay only", "text": "Remove cash on delivery; Razorpay only", "who": "Ritu Sharma", "line": 4, "quote": "But remove cash on delivery.", "replaces": ["P3"]}],
    "Feedback round": [
        {"type": "scope_change", "topic": "Contact form", "value": "Orange button", "text": "Change contact form button to orange", "who": "Arvind", "line": 0, "quote": "change the button colour on the contact form to orange"},
        {"type": "scope_change", "topic": "E-commerce", "value": "Sell protein powder", "text": "Client wants to sell protein powder on the site", "who": "Arvind", "line": 2, "quote": "we want people to buy our protein powder directly on the site", "outsideScope": True, "scopeNote": "e-commerce not included"}],
    "Menu page review": [
        {"type": "approval", "topic": "Menu page design", "value": "Green design", "text": "Green design is final", "who": "Suresh", "line": 1, "quote": "Green wala design final karo"},
        {"type": "action", "topic": "Photos", "value": "by Monday", "text": "Suresh sends photos", "owner": "Suresh", "due": "Monday tak", "dueDate": "2026-10-05", "line": 3, "quote": "Photos main Monday tak bhej dunga."},
        {"type": "decision", "topic": "Launch date", "value": "15 October", "text": "Website goes live on 15 October", "who": "Suresh", "line": 5, "quote": "Haan, 15 October theek hai."}],
    "Homepage copy": [
        {"type": "approval", "topic": "Homepage headline", "value": "Train smarter, not harder", "text": "Headline approved", "who": "Arvind", "line": 1, "quote": "\"Train smarter, not harder\" is approved."},
        {"type": "action", "topic": "Final copy", "value": "by Monday", "text": "Agency sends final copy", "owner": "Neha", "due": "by Monday", "dueDate": "2026-09-28", "line": 4, "quote": "Send me the final copy by Monday."}],
    "Design review": [
        {"type": "approval", "topic": "Theme", "value": "Beige", "text": "Beige theme approved", "who": "Asha", "line": 0, "quote": "I'm approving it from our side."},
        {"type": "decision", "topic": "Portfolio photos", "value": "10 photos", "text": "Use 10 project photos", "who": "Faisal", "line": 3, "quote": "keep it to 10 photos"}],
}


def _llm(answers):
    llm = FakeLLM()
    for title, items in answers.items():
        llm.on(f"CALL: {title},", {"summary": "x", "items": items})
    return llm


def test_ideal_answers_pass_every_check():
    llm = _llm(IDEAL)
    for case in load_cases():
        r = run_case(llm, case)
        failed = [c for c in r["checks"] if not c["ok"]]
        assert not failed, (case["id"], failed)


def test_wrong_answers_fail():
    bad = copy.deepcopy(IDEAL)
    for items in bad.values():
        for it in items:
            it.pop("replaces", None)
            it.pop("dueDate", None)
    bad["Homepage copy"].append({"type": "approval", "topic": "Blog", "value": "Free blog", "text": "Client approved a free blog",
                                 "who": "Arvind", "line": 2, "quote": "approved a free blog"})
    bad["Launch planning"].append({"type": "decision", "topic": "Launch date", "value": "20 October", "text": "Launch on 20 October",
                                   "who": "Faisal", "line": 1, "quote": "Let's launch on 20 October."})
    llm = _llm(bad)
    failed = {c["label"] for case in load_cases() for c in run_case(llm, case)["checks"] if not c["ok"]}
    assert failed == {
        "Descriptions due Friday, worked out as 18 Sep", "Agency sends a third variation, due 23 Sep",
        "Photo slider replaces the earlier homepage video", "20 October not left as a current launch date",
        "Launch moves to 12 October, replacing 5 October", "Razorpay only, replacing Razorpay + COD",
        "Photos from client by Monday, worked out as 5 Oct", "Final copy due Monday, worked out as 28 Sep",
        "No approval or agreement that the blog is included"}, failed
