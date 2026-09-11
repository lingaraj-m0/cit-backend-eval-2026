package main

import (
"net/http"
"net/http/httptest"
"testing"
)

// Tests the basic API health state
func TestHealthHandler(t *testing.T) {
req, err := http.NewRequest("GET", "/health", nil)
if err != nil {
t.Fatal(err)
}

rr := httptest.NewRecorder()
handler := http.HandlerFunc(healthHandler)
handler.ServeHTTP(rr, req)

if status := rr.Code; status != http.StatusOK {
t.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
}

expected := `{"status":"ok"}`
if rr.Body.String() != expected {
t.Errorf("handler returned unexpected body: got %v want %v", rr.Body.String(), expected)
}
}

// Tests the idempotency of the strict JSON parser (rejects duplicate keys)
func TestCheckStrictJSON(t *testing.T) {
validJSON := []byte(`{"record_id":"abc","payload":{},"work_ms":100,"timeout_ms":200}`)
if !checkStrictJSON(validJSON) {
t.Errorf("Expected valid JSON to pass strict check")
}

duplicateKeysJSON := []byte(`{"record_id":"abc","record_id":"def"}`)
if checkStrictJSON(duplicateKeysJSON) {
t.Errorf("Expected JSON with duplicate keys to fail and be rejected")
}

trailingGarbage := []byte(`{"record_id":"abc"}  extra`)
if checkStrictJSON(trailingGarbage) {
t.Errorf("Expected JSON with trailing garbage to fail")
}
}

// Tests retry limits and payload boundaries 
func TestValidateRecordFields(t *testing.T) {
validAttempts := 3
invalidAttempts := 10 // Contract specifies max 5

validRec := InputRecord{
RecordID:    "valid-id-123",
Payload:     []byte(`{}`),
WorkMs:      100,
TimeoutMs:   1000,
MaxAttempts: &validAttempts,
}

if !validateRecordFields(validRec) {
t.Errorf("Expected valid record with 3 attempts to pass")
}

invalidRec := validRec
invalidRec.MaxAttempts = &invalidAttempts
if validateRecordFields(invalidRec) {
t.Errorf("Expected record with 10 max attempts to be rejected")
}

invalidIDRec := validRec
invalidIDRec.RecordID = "invalid@id!"
if validateRecordFields(invalidIDRec) {
t.Errorf("Expected record with invalid ID characters to be rejected")
}
}
