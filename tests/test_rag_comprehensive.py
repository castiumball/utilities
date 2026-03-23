"""
Comprehensive RAG retrieval test suite.

Tests 8 categories of queries against the live Polaris server,
collecting the full streamed response and grading each on:
  - Whether retrieval fired (status events appeared)
  - Whether the response is relevant / correct
  - Whether images were included (for figure queries)

Usage:
    python tests/test_rag_comprehensive.py
"""

import json
import sys
import time
import requests

BASE_URL = "http://localhost:7999"
API = f"{BASE_URL}/api/chat"

# ============================================
# Test cases: (category, query, expected_behavior)
# ============================================
# expected_behavior keys:
#   retrieval: True/False — should retrieval fire?
#   keywords: list of strings that should appear in response (case-insensitive)
#   no_keywords: list of strings that should NOT appear (e.g. "reference material")
#   has_image: True if response should contain an image markdown tag

TEST_CASES = [
    # --- Category 1: No-retrieval (conversational / off-topic) ---
    {
        "category": "no-retrieval",
        "query": "Hello!",
        "expected": {
            "retrieval": False,
            "keywords": [],
            "no_keywords": ["reference material", "retrieved"],
        },
    },
    {
        "category": "no-retrieval",
        "query": "Thanks!",
        "expected": {
            "retrieval": False,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "no-retrieval",
        "query": "Tell me a joke about programming",
        "expected": {
            "retrieval": False,
            "keywords": [],
            "no_keywords": ["reference material", "section"],
        },
    },
    {
        "category": "no-retrieval",
        "query": "Tell me about the TV show Friends",
        "expected": {
            "retrieval": False,
            "keywords": [],
            "no_keywords": ["reference material", "STARS"],
        },
    },

    # --- Category 2: Exact figure ---
    {
        "category": "exact-figure",
        "query": "What is Figure 6?",
        "expected": {
            "retrieval": True,
            "keywords": ["coordinate system"],
            "no_keywords": ["reference material"],
            "has_image": True,
        },
    },
    {
        "category": "exact-figure",
        "query": "Show me Figure 1",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
            "has_image": True,
        },
    },

    # --- Category 3: Exact section ---
    {
        "category": "exact-section",
        "query": "What is in section 3.1?",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "exact-section",
        "query": "Summarize section 1.1",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },

    # --- Category 4: Exact table ---
    {
        "category": "exact-table",
        "query": "What does Table 1 show?",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },

    # --- Category 5: Keyword / concept ---
    {
        "category": "keyword",
        "query": "What is MDT?",
        "expected": {
            "retrieval": True,
            "keywords": ["minutia"],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "keyword",
        "query": "What file formats does the system support?",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "keyword",
        "query": "What is the coordinate system used?",
        "expected": {
            "retrieval": True,
            "keywords": ["coordinate"],
            "no_keywords": ["reference material"],
        },
    },

    # --- Category 6: Semantic / inferential ---
    {
        "category": "semantic",
        "query": "How does the system handle errors?",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "semantic",
        "query": "What are the main components of the software?",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },

    # --- Category 7: Cross-reference ---
    {
        "category": "cross-ref",
        "query": "What section discusses Figure 6?",
        "expected": {
            "retrieval": True,
            "keywords": ["coordinate"],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "cross-ref",
        "query": "What tables are in the document?",
        "expected": {
            "retrieval": True,
            "keywords": ["table"],
            "no_keywords": ["reference material"],
        },
    },

    # --- Category 8: Adversarial / edge ---
    {
        "category": "adversarial",
        "query": "What is section 99.99?",
        "expected": {
            "retrieval": True,
            "keywords": ["don't have", "not found", "no information",
                         "does not exist", "not available", "don't have detailed",
                         "unable to find", "no section"],
            "keywords_mode": "any",  # any ONE of these is fine
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "adversarial",
        "query": "Explain the quantum radar module",
        "expected": {
            "retrieval": True,
            "keywords": [],
            "no_keywords": ["reference material"],
        },
    },
    {
        "category": "adversarial",
        "query": "What is 2 + 2?",
        "expected": {
            # Retrieval may fire (5 words, no skip pattern) — acceptable.
            # The key check is that the answer is still correct.
            "keywords": ["4"],
            "no_keywords": ["reference material"],
        },
    },
]


def send_message(conversation_id: str, message: str):
    """Send a message and collect the full SSE stream."""
    url = f"{API}/conversations/{conversation_id}/completions"
    resp = requests.post(
        url,
        json={"message": message},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()

    tokens = []
    statuses = []
    had_retrieval = False

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
            if "token" in data:
                tokens.append(data["token"])
            if "status" in data and data["status"]:
                statuses.append(data["status"])
                had_retrieval = True
        except json.JSONDecodeError:
            pass

    return "".join(tokens), statuses, had_retrieval


def create_conversation():
    """Create a fresh conversation for each test."""
    resp = requests.post(f"{API}/conversations")
    resp.raise_for_status()
    return resp.json()["conversation"]["id"]


def grade_test(response: str, had_retrieval: bool, expected: dict):
    """Grade a test case. Returns (passed: bool, reason: str)."""
    issues = []

    # Check retrieval expectation
    if expected.get("retrieval") is True and not had_retrieval:
        issues.append("retrieval expected but didn't fire")
    elif expected.get("retrieval") is False and had_retrieval:
        issues.append("retrieval fired but shouldn't have")

    # Check required keywords
    resp_lower = response.lower()
    keywords = expected.get("keywords", [])
    mode = expected.get("keywords_mode", "all")

    if keywords:
        if mode == "any":
            if not any(kw.lower() in resp_lower for kw in keywords):
                issues.append(f"none of expected keywords found: {keywords[:3]}")
        else:
            for kw in keywords:
                if kw.lower() not in resp_lower:
                    issues.append(f"missing keyword: '{kw}'")

    # Check forbidden keywords
    for kw in expected.get("no_keywords", []):
        if kw.lower() in resp_lower:
            issues.append(f"found forbidden phrase: '{kw}'")

    # Check image
    if expected.get("has_image"):
        if "![" not in response or "](" not in response:
            issues.append("expected image markdown not found")

    if issues:
        return False, "; ".join(issues)
    return True, "OK"


def main():
    print("=" * 80)
    print("Polaris RAG Comprehensive Test Suite")
    print("=" * 80)

    # Check server is up
    try:
        requests.get(f"{BASE_URL}/api/health", timeout=5)
    except Exception:
        # Try root
        try:
            requests.get(BASE_URL, timeout=5)
        except Exception:
            print("ERROR: Server not reachable at", BASE_URL)
            sys.exit(1)

    results = []
    total = len(TEST_CASES)

    for i, tc in enumerate(TEST_CASES, 1):
        category = tc["category"]
        query = tc["query"]
        expected = tc["expected"]

        print(f"\n[{i}/{total}] [{category}] {query}")

        try:
            conv_id = create_conversation()
            start = time.time()
            response, statuses, had_retrieval = send_message(conv_id, query)
            elapsed = time.time() - start

            passed, reason = grade_test(response, had_retrieval, expected)
            status_icon = "PASS" if passed else "FAIL"

            # Truncate response for display
            resp_preview = response[:120].replace("\n", " ")
            if len(response) > 120:
                resp_preview += "..."

            print(f"  {status_icon} ({elapsed:.1f}s) — {reason}")
            print(f"  Response: {resp_preview}")
            if statuses:
                print(f"  Status events: {statuses}")

            results.append({
                "category": category,
                "query": query,
                "passed": passed,
                "reason": reason,
                "time": elapsed,
                "retrieval": had_retrieval,
                "response_len": len(response),
                "has_image": "![" in response,
            })

        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({
                "category": category,
                "query": query,
                "passed": False,
                "reason": f"Error: {exc}",
                "time": 0,
                "retrieval": False,
                "response_len": 0,
                "has_image": False,
            })

    # Summary table
    print("\n" + "=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)
    print(f"{'#':<3} {'Category':<16} {'Query':<42} {'Result':<6} {'Time':<6} {'RAG':<5} {'Img':<4}")
    print("-" * 80)

    passed_count = 0
    for i, r in enumerate(results, 1):
        q = r["query"][:40]
        status = "PASS" if r["passed"] else "FAIL"
        rag = "Yes" if r["retrieval"] else "No"
        img = "Yes" if r["has_image"] else ""
        t = f"{r['time']:.1f}s"
        print(f"{i:<3} {r['category']:<16} {q:<42} {status:<6} {t:<6} {rag:<5} {img:<4}")
        if r["passed"]:
            passed_count += 1

    print("-" * 80)
    print(f"Total: {passed_count}/{total} passed")

    # Show failures detail
    failures = [r for r in results if not r["passed"]]
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for r in failures:
            print(f"  [{r['category']}] {r['query']}")
            print(f"    Reason: {r['reason']}")

    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
