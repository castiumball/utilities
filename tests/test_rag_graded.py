"""
Comprehensive RAG Graded Test Suite
====================================
Sends questions across 8 categories to the live Polaris server,
captures responses (including retrieval status and content), and
grades each response.
"""

import json
import re
import requests
import sys

BASE_URL = "http://localhost:7999"

# ── Test cases ─────────────────────────────────────────────────
# Each test: (category, question, grading_criteria)
#   grading_criteria is a dict with:
#     - retrieval_expected: bool — should retrieval fire?
#     - keywords: list[str] — at least one must appear in response
#     - forbidden: list[str] — none should appear
#     - image_expected: bool — should response contain an image?

TESTS = [
    # ── Category 1: No-Retrieval (conversational) ──────────────
    ("no-retrieval", "Hello, how are you?", {
        "retrieval_expected": False,
        "keywords": ["hello", "hi", "hey", "help", "doing"],
        "forbidden": ["section", "document", "reference material"],
        "image_expected": False,
    }),
    ("no-retrieval", "What is 2 + 2?", {
        "retrieval_expected": False,
        "keywords": ["4", "four"],
        "forbidden": ["section", "document"],
        "image_expected": False,
    }),
    ("no-retrieval", "Tell me a fun fact about the TV show Friends", {
        "retrieval_expected": False,
        "keywords": ["friends", "ross", "rachel", "joey", "chandler", "monica", "phoebe", "show", "sitcom", "tv"],
        "forbidden": ["section", "document", "reference material", "knowledge base"],
        "image_expected": False,
    }),
    ("no-retrieval", "Thanks!", {
        "retrieval_expected": False,
        "keywords": ["welcome", "glad", "help", "happy", "anytime", "sure"],
        "forbidden": [],
        "image_expected": False,
    }),

    # ── Category 2: Exact Figure ───────────────────────────────
    ("exact-figure", "What is Figure 6?", {
        "retrieval_expected": True,
        "keywords": ["figure 6", "coordinate"],
        "forbidden": ["reference material"],
        "image_expected": True,
    }),
    ("exact-figure", "Show me Figure 1", {
        "retrieval_expected": True,
        "keywords": ["figure 1"],
        "forbidden": ["reference material"],
        "image_expected": True,
    }),

    # ── Category 3: Exact Section ──────────────────────────────
    ("exact-section", "What is in section 3.1?", {
        "retrieval_expected": True,
        "keywords": ["3.1"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
    ("exact-section", "Summarize section 3.1.3.1", {
        "retrieval_expected": True,
        "keywords": ["3.1.3.1", "coordinate", "fingerprint", "minutia", "image"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
    # NOTE: Section 1.2.3 ("Deleted") only exists in production Neo4j.
    # This test validates exact-match bypass of content-length filter.
    ("exact-section", "What does section 1.2.3 say?", {
        "retrieval_expected": True,
        "keywords": ["1.2.3", "deleted", "delete", "don't have", "not available"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),

    # ── Category 4: Exact Table ────────────────────────────────
    ("exact-table", "What does Table 1 show?", {
        "retrieval_expected": True,
        "keywords": ["table 1"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),

    # ── Category 5: Keyword/Concept ────────────────────────────
    ("keyword", "What is MDT?", {
        "retrieval_expected": True,
        "keywords": ["minutia", "deviation", "tool", "mdt"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
    ("keyword", "How does fingerprint matching work?", {
        "retrieval_expected": True,
        "keywords": ["fingerprint", "minutia", "match"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
    ("keyword", "What file formats are supported?", {
        "retrieval_expected": True,
        "keywords": ["file", "format"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),

    # ── Category 6: Semantic/Inferential ───────────────────────
    ("semantic", "What are the system requirements for the MDT?", {
        "retrieval_expected": True,
        "keywords": ["system", "requirement"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
    ("semantic", "How do the components of the system communicate?", {
        "retrieval_expected": True,
        "keywords": ["interface", "component", "communicat", "data"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),

    # ── Category 7: Cross-Reference ────────────────────────────
    ("cross-ref", "What section discusses Figure 6?", {
        "retrieval_expected": True,
        "keywords": ["figure 6", "section"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),

    # ── Category 8: Adversarial/Edge ───────────────────────────
    ("adversarial", "What is section 9999?", {
        "retrieval_expected": True,
        "keywords": ["not", "no", "don't", "doesn't", "does not", "unavailable", "exist"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
    ("adversarial", "Explain the quantum radar module", {
        "retrieval_expected": True,
        "keywords": ["not", "no", "don't", "doesn't", "does not", "mention", "information"],
        "forbidden": ["reference material"],
        "image_expected": False,
    }),
]


def send_question(question: str) -> dict:
    """Send a question and parse the SSE stream. Returns parsed response info."""

    # Create a new conversation
    conv = requests.post(f"{BASE_URL}/api/chat/conversations", json={"title": "Test"})
    conv_id = conv.json()["conversation"]["id"]

    # Stream the completion (this also saves the user message)
    resp = requests.post(
        f"{BASE_URL}/api/chat/conversations/{conv_id}/completions",
        json={"message": question},
        headers={"Accept": "text/event-stream"},
        stream=True,
    )

    tokens = []
    statuses = []
    has_image = False
    retrieval_fired = False

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        if "status" in data:
            statuses.append(data["status"])
            if any(kw in data["status"].lower() for kw in ["searching", "analyzing", "found", "refin"]):
                retrieval_fired = True
        if "token" in data:
            tokens.append(data["token"])

    full_response = "".join(tokens)
    has_image = bool(re.search(r'!\[.*?\]\(.*?\)|<img\s', full_response))

    return {
        "response": full_response,
        "statuses": statuses,
        "retrieval_fired": retrieval_fired,
        "has_image": has_image,
    }


def grade(result: dict, criteria: dict) -> dict:
    """Grade a response against criteria. Returns grade dict."""
    issues = []
    response_lower = result["response"].lower()

    # Check retrieval behavior
    if criteria["retrieval_expected"] and not result["retrieval_fired"]:
        issues.append("Retrieval should have fired but didn't")
    if not criteria["retrieval_expected"] and result["retrieval_fired"]:
        issues.append("Retrieval fired but shouldn't have")

    # Check keywords
    if criteria["keywords"]:
        found = any(kw.lower() in response_lower for kw in criteria["keywords"])
        if not found:
            issues.append(f"Missing expected keywords: {criteria['keywords']}")

    # Check forbidden phrases
    for phrase in criteria["forbidden"]:
        if phrase.lower() in response_lower:
            issues.append(f"Contains forbidden phrase: '{phrase}'")

    # Check image
    if criteria["image_expected"] and not result["has_image"]:
        issues.append("Expected image in response but none found")

    passed = len(issues) == 0
    return {"passed": passed, "issues": issues}


def main():
    print(f"\n{'='*80}")
    print("POLARIS RAG COMPREHENSIVE GRADED TEST SUITE")
    print(f"{'='*80}\n")

    results = []
    total = len(TESTS)

    for i, (category, question, criteria) in enumerate(TESTS, 1):
        print(f"[{i}/{total}] {category}: {question[:60]}...", end=" ", flush=True)
        try:
            result = send_question(question)
            grade_result = grade(result, criteria)
            status = "PASS" if grade_result["passed"] else "FAIL"
            print(status)
            if not grade_result["passed"]:
                for issue in grade_result["issues"]:
                    print(f"       -> {issue}")
            results.append({
                "category": category,
                "question": question,
                "status": status,
                "issues": grade_result["issues"],
                "response_preview": result["response"][:150],
                "retrieval_fired": result["retrieval_fired"],
                "has_image": result["has_image"],
                "statuses": result["statuses"],
            })
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "category": category,
                "question": question,
                "status": "ERROR",
                "issues": [str(e)],
                "response_preview": "",
                "retrieval_fired": False,
                "has_image": False,
                "statuses": [],
            })

    # Summary table
    print(f"\n{'='*80}")
    print("RESULTS SUMMARY")
    print(f"{'='*80}")
    print(f"{'Category':<15} {'Question':<45} {'Result':<6} {'Retrieval':<10} {'Image'}")
    print(f"{'-'*15} {'-'*45} {'-'*6} {'-'*10} {'-'*6}")
    for r in results:
        q = r["question"][:43] + ".." if len(r["question"]) > 45 else r["question"]
        retr = "Yes" if r["retrieval_fired"] else "No"
        img = "Yes" if r["has_image"] else "No"
        print(f"{r['category']:<15} {q:<45} {r['status']:<6} {retr:<10} {img}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    print(f"\nTotal: {total} | Passed: {passed} | Failed: {failed} | Errors: {errors}")

    # Print response previews for failed tests
    failed_results = [r for r in results if r["status"] != "PASS"]
    if failed_results:
        print(f"\n{'='*80}")
        print("FAILED TEST DETAILS")
        print(f"{'='*80}")
        for r in failed_results:
            print(f"\n[{r['category']}] {r['question']}")
            print(f"  Issues: {', '.join(r['issues'])}")
            print(f"  Statuses: {r['statuses']}")
            print(f"  Response: {r['response_preview']}")

    sys.exit(0 if failed == 0 and errors == 0 else 1)


if __name__ == "__main__":
    main()
