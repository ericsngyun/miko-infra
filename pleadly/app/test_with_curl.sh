#!/bin/bash
# Test Pleadly Intelligence Plane endpoints with curl
#
# Usage: ./test_with_curl.sh [endpoint]
# Example: ./test_with_curl.sh classify

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"

# Test health endpoint (no HMAC required)
test_health() {
    echo "=== Testing /health ==="
    curl -s "$BASE_URL/health" | python -m json.tool
    echo ""
}

# Test classify endpoint
test_classify() {
    echo "=== Testing /classify ==="

    PAYLOAD='{
  "documentText": "RADIOLOGY REPORT\n\nPatient: John Doe\nMRN: 123456\nExam: MRI Brain with contrast\nDate: 2024-03-01\n\nCLINICAL INDICATION: Head trauma following motor vehicle accident\n\nTECHNIQUE: MRI brain performed with and without IV contrast\n\nFINDINGS:\nThere is a small subdural hematoma along the right frontal convexity.\nNo midline shift. No herniation. Ventricles are normal in size.\n\nIMPRESSION:\n1. Small right frontal subdural hematoma\n2. No mass effect or midline shift\n\nDr. Jane Smith, MD\nBoard Certified Radiologist",
  "firmContext": {
    "firmName": "Test Law Firm",
    "jurisdiction": "CA",
    "practiceAreas": ["personal_injury"]
  },
  "organizationId": "org_test",
  "documentId": "doc_001"
}'

    curl -s -X POST "$BASE_URL/classify" \
        -H "Content-Type: application/json" \
        -H "X-Request-Id: test-classify-$(date +%s)" \
        -d "$PAYLOAD" | python -m json.tool
    echo ""
}

# Test intake endpoint
test_intake() {
    echo "=== Testing /intake ==="

    PAYLOAD='{
  "callerName": "John Doe",
  "accidentType": "Motor vehicle accident",
  "accidentDate": "2024-02-15",
  "injuriesDescribed": "Neck and back pain, concussion, broken wrist",
  "soughtMedicalTreatment": true,
  "otherPartyAtFault": true,
  "jurisdiction": "CA"
}'

    curl -s -X POST "$BASE_URL/intake" \
        -H "Content-Type: application/json" \
        -H "X-Request-Id: test-intake-$(date +%s)" \
        -d "$PAYLOAD" | python -m json.tool
    echo ""
}

# Test SOL endpoint
test_sol() {
    echo "=== Testing /sol-scan ==="

    PAYLOAD='{
  "jurisdiction": "CA",
  "caseType": "general_pi",
  "incidentDate": "2024-02-15",
  "clientDob": "1990-05-20",
  "isMinor": false,
  "governmentEntity": false
}'

    curl -s -X POST "$BASE_URL/sol-scan" \
        -H "Content-Type: application/json" \
        -H "X-Request-Id: test-sol-$(date +%s)" \
        -d "$PAYLOAD" | python -m json.tool
    echo ""
}

# Test analyze endpoint
test_analyze() {
    echo "=== Testing /analyze ==="

    PAYLOAD='{
  "documentText": "PATIENT CARE REPORT\n\nPatient: John Doe\nDOB: 05/20/1990\nProvider: Dr. Sarah Johnson\nDate of Service: 03/01/2024\n\nChief Complaint: Lower back pain following motor vehicle accident\n\nHPI: Patient reports being rear-ended at a stoplight on 02/15/2024. Immediate onset of lower back pain radiating to left leg. Pain level 8/10.\n\nDiagnosis: Lumbar strain, possible disc herniation\n\nTreatment: Pain medication prescribed, physical therapy recommended, MRI ordered",
  "analysisType": "medical_record",
  "firmContext": {
    "firmName": "Test Law Firm",
    "jurisdiction": "CA",
    "practiceAreas": ["personal_injury"]
  },
  "organizationId": "org_test",
  "caseId": "case_001",
  "documentId": "doc_002"
}'

    curl -s -X POST "$BASE_URL/analyze" \
        -H "Content-Type: application/json" \
        -H "X-Request-Id: test-analyze-$(date +%s)" \
        -d "$PAYLOAD" | python -m json.tool
    echo ""
}

# Main
case "${1:-all}" in
    health)
        test_health
        ;;
    classify)
        test_classify
        ;;
    intake)
        test_intake
        ;;
    sol)
        test_sol
        ;;
    analyze)
        test_analyze
        ;;
    all)
        test_health
        test_classify
        test_intake
        test_sol
        test_analyze
        ;;
    *)
        echo "Usage: $0 [health|classify|intake|sol|analyze|all]"
        exit 1
        ;;
esac
