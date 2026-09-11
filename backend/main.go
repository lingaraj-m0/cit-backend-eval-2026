package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	_ "github.com/lib/pq"
)

var db *sql.DB
var validFilename = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.jsonl$`)
var validRecordID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`)

const (
	minWorkMs         = 25
	maxWorkMs         = 60000
	minTimeoutMs      = 25
	maxTimeoutMs      = 120000
	minMaxAttempts    = 1
	maxMaxAttempts    = 5
	maxPayloadBytes   = 16384
	maxArchiveRecords = 100000
)

// validateRecordFields enforces the field-level bounds from CONTRACT.md that
// were previously unchecked (only non-zero was checked before).
func validateRecordFields(rec InputRecord) bool {
	if !validRecordID.MatchString(rec.RecordID) {
		return false
	}
	if rec.Payload == nil || len(rec.Payload) > maxPayloadBytes {
		return false
	}
	if rec.WorkMs < minWorkMs || rec.WorkMs > maxWorkMs {
		return false
	}
	if rec.TimeoutMs < minTimeoutMs || rec.TimeoutMs > maxTimeoutMs {
		return false
	}
	if rec.MaxAttempts != nil {
		if *rec.MaxAttempts < minMaxAttempts || *rec.MaxAttempts > maxMaxAttempts {
			return false
		}
	}
	return true
}

type JobResponse struct {
	JobID  string `json:"job_id"`
	Status string `json:"status"`
}

type InputRecord struct {
	RecordID    string          `json:"record_id"`
	Payload     json.RawMessage `json:"payload"`
	WorkMs      int             `json:"work_ms"`
	TimeoutMs   int             `json:"timeout_ms"`
	MaxAttempts *int            `json:"max_attempts"`
}

type ProcessorRequest struct {
	RunID   string          `json:"run_id"`
	JobID   string          `json:"job_id"`
	TaskID  string          `json:"task_id"`
	Attempt int             `json:"attempt"`
	Record  json.RawMessage `json:"record"`
}

type ProcessorResponse struct {
	RecordID string `json:"record_id"`
	Attempt  int    `json:"attempt"`
	Value    int64  `json:"value"`
	Receipt  string `json:"receipt"`
}

type ResultResponse struct {
	JobID  string         `json:"job_id"`
	Status string         `json:"status"`
	Files  []FileResult   `json:"files"`
	Totals TotalsResult   `json:"totals"`
}

type FileResult struct {
	TaskID           string         `json:"task_id"`
	Status           string         `json:"status"`
	RecordsTotal     int            `json:"records_total"`
	RecordsSucceeded int            `json:"records_succeeded"`
	RecordsFailed    int            `json:"records_failed"`
	ValueSum         int64          `json:"value_sum"`
	Records          []RecordResult `json:"records"`
}

type RecordResult struct {
	RecordID  string `json:"record_id"`
	Status    string `json:"status"`
	Attempts  int    `json:"attempts"`
	Value     *int64 `json:"value,omitempty"`
	Receipt   string `json:"receipt,omitempty"`
	ErrorCode string `json:"error_code,omitempty"`
}

type TotalsResult struct {
	Files     int   `json:"files"`
	Records   int   `json:"records"`
	Succeeded int   `json:"succeeded"`
	Failed    int   `json:"failed"`
	ValueSum  int64 `json:"value_sum"`
}

func main() {
	var err error
	connStr := "user=postgres password=postgres dbname=batch_service sslmode=disable"
	db, err = sql.Open("postgres", connStr)
	if err != nil {
		log.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	if err = db.Ping(); err != nil {
		log.Fatalf("Database unreachable: %v", err)
	}
	fmt.Println("Connected to PostgreSQL successfully.")

	if res, err := db.Exec("UPDATE records SET status = 'pending' WHERE status = 'running'"); err != nil {
		log.Printf("warning: failed to reclaim in-flight records on startup: %v", err)
	} else if n, _ := res.RowsAffected(); n > 0 {
		log.Printf("reclaimed %d record(s) left 'running' from a previous process", n)
	}

	os.MkdirAll("uploads", os.ModePerm)

	for i := 0; i < 20; i++ {
		go workerLoop()
	}

	http.HandleFunc("/health", healthHandler)
	http.HandleFunc("/jobs", jobsHandler)
	http.HandleFunc("/jobs/", jobRouterHandler)

	port := "8081"
	fmt.Printf("Batch service API listening on port %s...\n", port)
	if err := http.ListenAndServe(":"+port, nil); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status":"ok"}`))
}

func jobsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodPost {
		handleCreateJob(w, r)
	} else {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
	}
}

func jobRouterHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/jobs/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		http.Error(w, "Not found", http.StatusNotFound)
		return
	}

	jobID := parts[0]

	if len(parts) == 1 {
		handleGetJobProgress(w, jobID)
	} else if len(parts) == 2 && parts[1] == "result" {
		handleGetJobResults(w, jobID)
	} else {
		http.Error(w, "Not found", http.StatusNotFound)
	}
}

func handleGetJobProgress(w http.ResponseWriter, jobID string) {
	var status string
	var totalFiles, filesTerminal int
	var ingestionComplete bool

	err := db.QueryRow(`SELECT status, COALESCE(total_files,0), COALESCE(files_terminal,0), COALESCE(ingestion_complete, false) FROM jobs WHERE id = $1`, jobID).Scan(&status, &totalFiles, &filesTerminal, &ingestionComplete)
	if err == sql.ErrNoRows {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	} else if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	var recTotal, recSucc, recFail, recRun, recPend int
	db.QueryRow(`SELECT count(*),
		COUNT(*) FILTER (WHERE status = 'SUCCEEDED'),
		COUNT(*) FILTER (WHERE status = 'FAILED'),
		COUNT(*) FILTER (WHERE status = 'running'),
		COUNT(*) FILTER (WHERE status = 'pending')
		FROM records WHERE job_id = $1`, jobID).Scan(&recTotal, &recSucc, &recFail, &recRun, &recPend)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"job_id":             jobID,
		"status":             status,
		"ingestion_complete": ingestionComplete,
		"files_total":        totalFiles,
		"files_terminal":     filesTerminal,
		"records_total":      recTotal,
		"records_succeeded":  recSucc,
		"records_failed":     recFail,
		"records_running":    recRun,
		"records_pending":    recPend,
	})
}

func handleGetJobResults(w http.ResponseWriter, jobID string) {
	var status string
	var totalFiles, recTotal, recSucc, recFail int
	var valSum int64

	err := db.QueryRow(`SELECT status, COALESCE(total_files,0), COALESCE(records_total,0), 
	                    COALESCE(records_succeeded,0), COALESCE(records_failed,0), COALESCE(value_sum,0) 
	                    FROM jobs WHERE id = $1`, jobID).Scan(&status, &totalFiles, &recTotal, &recSucc, &recFail, &valSum)
	if err == sql.ErrNoRows {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	}

	if status == "QUEUED" || status == "RUNNING" {
		http.Error(w, "Job not terminal", http.StatusConflict)
		return
	}

	resp := ResultResponse{
		JobID:  jobID,
		Status: status,
		Totals: TotalsResult{
			Files:     totalFiles,
			Records:   recTotal,
			Succeeded: recSucc,
			Failed:    recFail,
			ValueSum:  valSum,
		},
		Files: make([]FileResult, 0),
	}

	fileRows, _ := db.Query(`SELECT id, task_id, status, COALESCE(records_total,0) FROM files WHERE job_id = $1`, jobID)
	defer fileRows.Close()

	for fileRows.Next() {
		var fID, taskID, fStatus string
		var fRecTot int
		fileRows.Scan(&fID, &taskID, &fStatus, &fRecTot)

		fileRes := FileResult{
			TaskID:       taskID,
			Status:       fStatus,
			RecordsTotal: fRecTot,
			Records:      make([]RecordResult, 0),
		}

		recRows, _ := db.Query(`SELECT record_id, status, COALESCE(attempts,0), value, receipt, error_code FROM records WHERE file_id = $1`, fID)
		for recRows.Next() {
			var rID, rStatus string
			var attempts int
			var rVal sql.NullInt64
			var rRec, rErrCode sql.NullString

			recRows.Scan(&rID, &rStatus, &attempts, &rVal, &rRec, &rErrCode)

			rr := RecordResult{
				RecordID: rID,
				Status:   rStatus,
				Attempts: attempts,
			}
			if rVal.Valid {
				v := rVal.Int64
				rr.Value = &v
			}
			if rRec.Valid {
				rr.Receipt = rRec.String
			}
			if rErrCode.Valid {
				rr.ErrorCode = rErrCode.String
			}
			fileRes.Records = append(fileRes.Records, rr)
		}
		recRows.Close()

		var fSucc, fFail int
		var fValSum int64
		for _, rec := range fileRes.Records {
			if rec.Status == "SUCCEEDED" {
				fSucc++
				if rec.Value != nil {
					fValSum += *rec.Value
				}
			} else if rec.Status == "FAILED" || rec.Status == "ATTEMPTS_EXHAUSTED" {
				fFail++
			}
		}

		fileRes.RecordsSucceeded = fSucc
		fileRes.RecordsFailed = fFail
		fileRes.ValueSum = fValSum

		resp.Files = append(resp.Files, fileRes)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func handleCreateJob(w http.ResponseWriter, r *http.Request) {
	runID := r.Header.Get("X-Run-ID")
	idempotencyKey := r.Header.Get("Idempotency-Key")

	if runID == "" || idempotencyKey == "" {
		http.Error(w, "Missing X-Run-ID or Idempotency-Key header", http.StatusBadRequest)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, 64<<20)
	if err := r.ParseMultipartForm(64 << 20); err != nil {
		http.Error(w, "Payload too large or invalid multipart form", http.StatusRequestEntityTooLarge)
		return
	}

	file, _, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "Missing 'file' field", http.StatusBadRequest)
		return
	}
	defer file.Close()

	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		http.Error(w, "Failed to read file", http.StatusInternalServerError)
		return
	}
	fileHash := hex.EncodeToString(hasher.Sum(nil))

	var existingJobID, existingHash string
	err = db.QueryRow("SELECT id, archive_hash FROM jobs WHERE idempotency_key = $1 AND run_id = $2", idempotencyKey, runID).Scan(&existingJobID, &existingHash)

	w.Header().Set("Content-Type", "application/json")
	if err == sql.ErrNoRows {
		tmpPath := filepath.Join("uploads", idempotencyKey+".zip")
		file.Seek(0, io.SeekStart)
		dst, _ := os.Create(tmpPath)
		io.Copy(dst, file)
		dst.Close()

		if !validateZip(tmpPath) {
			os.Remove(tmpPath)
			http.Error(w, "Invalid archive contents", http.StatusBadRequest)
			return
		}

		var newJobID string
		insertQuery := `INSERT INTO jobs (idempotency_key, run_id, archive_hash, status, ingestion_complete) VALUES ($1, $2, $3, 'QUEUED', FALSE) RETURNING id`
		err = db.QueryRow(insertQuery, idempotencyKey, runID, fileHash).Scan(&newJobID)
		if err != nil {
			var raceJobID, raceHash string
			lookupErr := db.QueryRow("SELECT id, archive_hash FROM jobs WHERE idempotency_key = $1 AND run_id = $2", idempotencyKey, runID).Scan(&raceJobID, &raceHash)
			os.Remove(tmpPath)
			if lookupErr != nil {
				http.Error(w, "Database error", http.StatusInternalServerError)
				return
			}
			if raceHash != fileHash {
				http.Error(w, "Idempotency key used with different file contents", http.StatusConflict)
				return
			}
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(JobResponse{JobID: raceJobID, Status: "QUEUED"})
			return
		}

		finalZipPath := filepath.Join("uploads", newJobID+".zip")
		os.Rename(tmpPath, finalZipPath)

		go processJobAsync(newJobID, finalZipPath)

		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(JobResponse{JobID: newJobID, Status: "QUEUED"})
		return

	} else if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	if existingHash != fileHash {
		http.Error(w, "Idempotency key used with different file contents", http.StatusConflict)
		return
	}

	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(JobResponse{JobID: existingJobID, Status: "QUEUED"})
}

func checkStrictJSON(line []byte) bool {
	dec := json.NewDecoder(bytes.NewReader(line))
	dec.UseNumber()
	t, err := dec.Token()
	if err != nil || t != json.Delim('{') { return false }

	keys := make(map[string]bool)
	for dec.More() {
		t, err := dec.Token()
		if err != nil { return false }
		key, ok := t.(string)
		if !ok { return false }

		if keys[key] { return false }
		keys[key] = true

		var val interface{}
		if err := dec.Decode(&val); err != nil { return false }
	}

	t, err = dec.Token()
	if err != nil || t != json.Delim('}') { return false }
	if dec.More() { return false }
	return true
}

func validateZip(path string) bool {
	zr, err := zip.OpenReader(path)
	if err != nil {
		return false
	}
	defer zr.Close()

	if len(zr.File) == 0 || len(zr.File) > 1000 {
		return false
	}

	seenFilenames := make(map[string]bool)
	var totalUncompressed uint64
	totalRecordsInArchive := 0

	for _, f := range zr.File {
		name := f.Name
		if f.FileInfo().IsDir() || strings.Contains(name, "/") || strings.Contains(name, "\\") || strings.Contains(name, "..") {
			return false
		}
		if !validFilename.MatchString(name) {
			return false
		}
		if seenFilenames[name] {
			return false
		}
		seenFilenames[name] = true

		totalUncompressed += f.UncompressedSize64
		if totalUncompressed > 256<<20 {
			return false
		}

		rc, err := f.Open()
		if err != nil {
			return false
		}

		scanner := bufio.NewScanner(rc)
		buf := make([]byte, 1024*1024)
		scanner.Buffer(buf, 1024*1024)

		lineCount := 0
		seenRecordIDs := make(map[string]bool)

		for scanner.Scan() {
			lineBytes := scanner.Bytes()
			if len(lineBytes) > 65536 {
				rc.Close()
				return false
			}

			line := strings.TrimSpace(string(lineBytes))
			if line == "" {
				continue
			}
			lineCount++

			if !checkStrictJSON([]byte(line)) {
				rc.Close()
				return false
			}

			dec := json.NewDecoder(strings.NewReader(line))
			dec.DisallowUnknownFields()
			var rec InputRecord
			if err := dec.Decode(&rec); err != nil {
				rc.Close()
				return false
			}

			if !validateRecordFields(rec) {
				rc.Close()
				return false
			}

			if seenRecordIDs[rec.RecordID] {
				rc.Close()
				return false
			}
			seenRecordIDs[rec.RecordID] = true

			totalRecordsInArchive++
			if totalRecordsInArchive > maxArchiveRecords {
				rc.Close()
				return false
			}
		}

		if err := scanner.Err(); err != nil {
			rc.Close()
			return false
		}

		rc.Close()
		if lineCount == 0 {
			return false
		}
	}
	return true
}

func processJobAsync(jobID, zipPath string) {
	zr, err := zip.OpenReader(zipPath)
	if err != nil {
		db.Exec("UPDATE jobs SET status = 'FAILED', ingestion_complete = TRUE WHERE id = $1", jobID)
		return
	}
	defer zr.Close()

	db.Exec("UPDATE jobs SET status = 'RUNNING', total_files = $1 WHERE id = $2", len(zr.File), jobID)

	totalRecords := 0

	for _, f := range zr.File {
		var fileID string
		err := db.QueryRow("INSERT INTO files (job_id, task_id, status) VALUES ($1, $2, 'pending') RETURNING id", jobID, f.Name).Scan(&fileID)
		if err != nil {
			continue
		}

		rc, err := f.Open()
		if err != nil {
			continue
		}

		scanner := bufio.NewScanner(rc)
		buf := make([]byte, 1024*1024)
		scanner.Buffer(buf, 1024*1024)

		fileRecords := 0
		for scanner.Scan() {
			line := scanner.Text()
			if strings.TrimSpace(line) == "" {
				continue
			}

			var rec InputRecord
			if err := json.Unmarshal([]byte(line), &rec); err != nil {
				continue
			}

			_, err = db.Exec(`INSERT INTO records (file_id, job_id, record_id, payload, status, work_ms, timeout_ms) 
				VALUES ($1, $2, $3, $4, 'pending', $5, $6) ON CONFLICT (file_id, record_id) DO NOTHING`,
				fileID, jobID, rec.RecordID, line, rec.WorkMs, rec.TimeoutMs)
			if err == nil {
				fileRecords++
				totalRecords++
			} else {
				log.Printf("DB insert failed for record %s (file %s): %v", rec.RecordID, f.Name, err)
			}
		}

		if err := scanner.Err(); err != nil {
			log.Printf("Scanner failed on file %s: %v", f.Name, err)
		}
		rc.Close()
		db.Exec("UPDATE files SET records_total = $1 WHERE id = $2", fileRecords, fileID)

		syncJobState(jobID)
	}

	_ = totalRecords
	db.Exec("UPDATE jobs SET ingestion_complete = TRUE WHERE id = $1", jobID)
	syncJobState(jobID)
}

func workerLoop() {
	for {
		var fileID, jobID, recID, payload, runID, taskID string
		var attempts int

		tx, err := db.Begin()
		if err != nil {
			time.Sleep(1 * time.Second)
			continue
		}

		err = tx.QueryRow(`
			SELECT r.file_id, r.job_id, r.record_id, r.payload, COALESCE(r.attempts, 0), j.run_id, f.task_id
			FROM records r
			JOIN jobs j ON r.job_id = j.id
			JOIN files f ON r.file_id = f.id
			WHERE r.status = 'pending'
			FOR UPDATE OF r SKIP LOCKED
			LIMIT 1
		`).Scan(&fileID, &jobID, &recID, &payload, &attempts, &runID, &taskID)

		if err != nil {
			tx.Rollback()
			time.Sleep(500 * time.Millisecond)
			continue
		}

		tx.Exec("UPDATE records SET status = 'running' WHERE file_id = $1 AND record_id = $2", fileID, recID)
		tx.Commit()

		var rec InputRecord
		json.Unmarshal([]byte(payload), &rec)
		maxAttempts := 3
		if rec.MaxAttempts != nil {
			maxAttempts = *rec.MaxAttempts
		}

		attempts++
		statusCode, procResp, retryAfter := processSingleRecord(runID, jobID, taskID, recID, payload, attempts)

		if statusCode == 0 {
			db.Exec("UPDATE records SET status = 'pending' WHERE file_id = $1 AND record_id = $2", fileID, recID)
		} else if statusCode == 200 && procResp != nil {
			db.Exec("UPDATE records SET status = 'SUCCEEDED', attempts = $1, value = $2, receipt = $3 WHERE file_id = $4 AND record_id = $5", attempts, procResp.Value, procResp.Receipt, fileID, recID)
		} else if statusCode == 429 {
			db.Exec("UPDATE records SET status = 'pending' WHERE file_id = $1 AND record_id = $2", fileID, recID)
			time.Sleep(time.Duration(retryAfter) * time.Second)
		} else {
			if attempts >= maxAttempts {
				db.Exec("UPDATE records SET status = 'FAILED', attempts = $1, error_code = 'ATTEMPTS_EXHAUSTED' WHERE file_id = $2 AND record_id = $3", attempts, fileID, recID)
			} else {
				db.Exec("UPDATE records SET status = 'pending', attempts = $1 WHERE file_id = $2 AND record_id = $3", attempts, fileID, recID)
			}
		}

		var activeCount int
		db.QueryRow("SELECT count(*) FROM records WHERE job_id = $1 AND status IN ('pending', 'running')", jobID).Scan(&activeCount)
		if activeCount == 0 {
			syncJobState(jobID)
		}
	}
}

func processSingleRecord(runID, jobID, taskID, recordID, rawPayload string, attempt int) (int, *ProcessorResponse, int) {
	reqBody := ProcessorRequest{
		RunID:   runID,
		JobID:   jobID,
		TaskID:  taskID,
		Attempt: attempt,
		Record:  json.RawMessage(rawPayload),
	}

	jsonData, _ := json.Marshal(reqBody)
	resp, err := http.Post("http://localhost:8001/v1/process", "application/json", bytes.NewReader(jsonData))
	if err != nil {
		log.Printf("processor call failed for record %s (attempt %d): %v", recordID, attempt, err)
		return 0, nil, 0
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusOK {
		var procResp ProcessorResponse
		if err := json.NewDecoder(resp.Body).Decode(&procResp); err != nil {
			return 0, nil, 0
		}
		return resp.StatusCode, &procResp, 0
	}

	retryAfter := 1
	if ra := resp.Header.Get("Retry-After"); ra != "" {
		if val, err := strconv.Atoi(ra); err == nil {
			retryAfter = val
		}
	}
	return resp.StatusCode, nil, retryAfter
}

func syncJobState(jobID string) {
	db.Exec(`
		UPDATE jobs 
		SET 
			records_total = (SELECT count(*) FROM records WHERE job_id = $1),
			records_pending = (SELECT count(*) FROM records WHERE job_id = $1 AND status = 'pending'),
			records_running = (SELECT count(*) FROM records WHERE job_id = $1 AND status = 'running'),
			records_succeeded = (SELECT count(*) FROM records WHERE job_id = $1 AND status = 'SUCCEEDED'),
			records_failed = (SELECT count(*) FROM records WHERE job_id = $1 AND status = 'FAILED'),
			value_sum = (SELECT COALESCE(sum(value), 0) FROM records WHERE job_id = $1 AND status = 'SUCCEEDED')
		WHERE id = $1
	`, jobID)

	db.Exec(`
		UPDATE files f
		SET 
			records_succeeded = (SELECT count(*) FROM records WHERE file_id = f.id AND status = 'SUCCEEDED'),
			records_failed = (SELECT count(*) FROM records WHERE file_id = f.id AND status = 'FAILED'),
			value_sum = (SELECT COALESCE(sum(value), 0) FROM records WHERE file_id = f.id AND status = 'SUCCEEDED')
		WHERE job_id = $1
	`, jobID)

	db.Exec(`
		UPDATE files
		SET status = CASE WHEN records_failed > 0 THEN 'FAILED' ELSE 'SUCCEEDED' END
		WHERE job_id = $1 AND records_total > 0 AND (records_succeeded + records_failed = records_total)
	`, jobID)

	db.Exec(`
		UPDATE jobs
		SET files_terminal = (SELECT count(*) FROM files WHERE job_id = $1 AND status IN ('SUCCEEDED', 'FAILED'))
		WHERE id = $1
	`, jobID)

	db.Exec(`
		UPDATE jobs
		SET status = CASE WHEN records_failed > 0 THEN 'FAILED' ELSE 'SUCCEEDED' END
		WHERE id = $1 
		  AND ingestion_complete = TRUE 
		  AND records_pending = 0 
		  AND records_running = 0
		  AND total_files = files_terminal
		  AND total_files > 0
	`, jobID)
}