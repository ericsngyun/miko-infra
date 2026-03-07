#!/usr/bin/env python3
"""
Test script for Pleadly Intelligence Plane endpoints.

Tests classify, intake, and demand endpoints with HMAC authentication.
"""

import hashlib
import hmac
import json
import time
import requests

# Configuration
BASE_URL = "http://localhost:8000"
HMAC_SECRET = ""  # Empty for development mode (no HMAC validation)


def make_authenticated_request(endpoint: str, payload: dict) -> dict:
    """Make an authenticated request with HMAC signature."""
    timestamp = str(int(time.time()))
    body_json = json.dumps(payload)

    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": f"test-{timestamp}",
    }

    if HMAC_SECRET:
        # Compute HMAC signature
        message = f"{timestamp}.{body_json}"
        signature = hmac.new(
            HMAC_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers["X-Pleadly-Signature"] = signature
        headers["X-Pleadly-Timestamp"] = timestamp

    response = requests.post(
        f"{BASE_URL}{endpoint}",
        headers=headers,
        data=body_json,
    )

    return response.json()


def test_classify():
    """Test document classification endpoint."""
    print("\n=== Testing /classify ===")

    payload = {
        "documentText": """
        RADIOLOGY REPORT

        Patient: John Doe
        MRN: 123456
        Exam: MRI Brain with contrast
        Date: 2024-03-01

        CLINICAL INDICATION: Head trauma following motor vehicle accident

        TECHNIQUE: MRI brain performed with and without IV contrast

        FINDINGS:
        There is a small subdural hematoma along the right frontal convexity.
        No midline shift. No herniation. Ventricles are normal in size.

        IMPRESSION:
        1. Small right frontal subdural hematoma
        2. No mass effect or midline shift

        Dr. Jane Smith, MD
        Board Certified Radiologist
        """,
        "firmContext": {
            "firmName": "Test Law Firm",
            "jurisdiction": "CA",
            "practiceAreas": ["personal_injury"]
        },
        "organizationId": "org_test",
        "documentId": "doc_001"
    }

    try:
        result = make_authenticated_request("/classify", payload)
        print(f"Result: {json.dumps(result, indent=2)}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_intake():
    """Test intake scoring endpoint."""
    print("\n=== Testing /intake ===")

    payload = {
        "callerName": "John Doe",
        "accidentType": "Motor vehicle accident",
        "accidentDate": "2024-02-15",
        "injuriesDescribed": "Neck and back pain, concussion, broken wrist",
        "soughtMedicalTreatment": True,
        "otherPartyAtFault": True,
        "jurisdiction": "CA"
    }

    try:
        result = make_authenticated_request("/intake", payload)
        print(f"Result: {json.dumps(result, indent=2)}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_demand():
    """Test demand letter generation endpoint."""
    print("\n=== Testing /demand ===")

    payload = {
        "caseSummary": """
        On February 15, 2024, plaintiff John Doe was driving southbound on Highway 101
        when defendant Jane Smith ran a red light at the intersection of Main Street,
        striking plaintiff's vehicle on the driver's side. Police report #2024-12345
        cites defendant for failure to obey traffic signal.
        """,
        "medicalSummary": """
        Plaintiff was transported to Memorial Hospital by ambulance. Emergency room
        diagnosis: concussion, cervical strain, fractured left wrist. Treated with
        pain medication and wrist cast. Follow-up care included 12 weeks physical
        therapy, orthopedic surgeon consultation, and ongoing chiropractic treatment.
        Prognosis: permanent limited range of motion in left wrist.
        """,
        "billingSummary": """
        Emergency room: $8,500
        Ambulance: $1,200
        Orthopedic surgery: $15,000
        Physical therapy (12 weeks): $4,800
        Chiropractic (ongoing): $3,200
        Total medical specials: $32,700
        """,
        "policeReport": "Report #2024-12345 - Defendant cited for red light violation",
        "demandAmount": None,
        "multiplier": 3.0,
        "instructions": "Emphasize permanent injury and lost wages",
        "firmContext": {
            "firmName": "Test Law Firm",
            "jurisdiction": "CA",
            "practiceAreas": ["personal_injury"],
            "caseContext": {
                "caseName": "Doe v. Smith",
                "clientName": "John Doe",
                "accidentDate": "2024-02-15"
            }
        },
        "organizationId": "org_test",
        "caseId": "case_001"
    }

    try:
        result = make_authenticated_request("/demand", payload)
        print(f"Result keys: {result.keys()}")
        if "letter" in result:
            print(f"Letter has {len(result['letter'].get('letterText', ''))} characters")
            print(f"Demand amount: ${result['letter'].get('demandAmount', 0):,.2f}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    """Run all tests."""
    print("Starting endpoint tests...")
    print(f"Base URL: {BASE_URL}")
    print(f"HMAC Mode: {'Enabled' if HMAC_SECRET else 'Disabled (dev mode)'}")

    results = {
        "classify": test_classify(),
        "intake": test_intake(),
        "demand": test_demand(),
    }

    print("\n=== Test Results ===")
    for endpoint, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{endpoint}: {status}")

    all_passed = all(results.values())
    if all_passed:
        print("\nAll tests passed!")
    else:
        print("\nSome tests failed. Check Ollama is running on localhost:11434")

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
