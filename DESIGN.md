# System Design & Architecture

## State Model
The application uses PostgreSQL as the single source of truth for all job, file, and record states. 
* **Jobs & Files:** Tracked via aggregations of their underlying records.
* **Records:** Transition through `pending` -> `running` -> `SUCCEEDED` / `FAILED`. 
Concurrency is managed using PostgreSQL's `FOR UPDATE SKIP LOCKED` mechanism. This allows multiple stateless workers to concurrently poll the `records` table for `pending` tasks without locking each other out or requiring a separate message broker.

## Crash and Restart Behavior
Worker and API processes are designed to be entirely stateless. If a worker crashes mid-processing, the record it was handling remains in the `running` state. To ensure no work is lost, the API executes a recovery query on startup (`UPDATE records SET status = 'pending' WHERE status = 'running'`). This safely reverts orphaned in-flight records back to the queue for the next available worker to pick up, strictly adhering to the safe retry contract.

## Tradeoffs & Optimizations
**The Database Bottleneck:** Initially, workers updated the parent `jobs` and `files` summary tables every time a single record completed. Under high concurrency (e.g., the 10,000-record burst workload), this resulted in tens of thousands of heavy `UPDATE` statements, deadlocking the database and causing the 600-second benchmark to time out.
**The Fix (Debouncing):** I optimized the worker loop to implement a "debounce" mechanism. Workers now only trigger the heavy `syncJobState` aggregation when the local active queue for that job reaches zero. Live progress tracking is instead calculated on-the-fly during GET requests using SQL `FILTER` clauses.

## Performance Measurements
Performance was tested against a local PostgreSQL instance and the provided Go backend using the supplied benchmark presets.
* **Hardware/Environment:** Local Windows environment, PostgreSQL, Go.
* **Smoke Test:** Passed (`Correct: True`)
* **Burst Test (10,000 records):** Passed (`Correct: True`) with a median valid makespan of **118.85 seconds**.

## Limitations
Because the workers use polling (`SKIP LOCKED`) rather than an event-driven pub/sub model, there is a minor latency overhead as workers sleep and wake to check for new records. While highly resilient and easy to deploy, scaling this to millions of rows would eventually require transitioning to a dedicated queue like Kafka to avoid database CPU exhaustion.
