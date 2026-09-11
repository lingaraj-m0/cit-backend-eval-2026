# Changes

## 1.1.0

- Added real-duration sustained and long-tail inputs, uneven-file workloads, multiple
  retry budgets, overlapping jobs, 1,000-file aggregation checks and optional
  10,000/100,000-record stress inputs. Kept the short development samples.
- Added valid Windows/UTF-8 line-format fixtures and Windows archive-path rejection
  cases. The corpus now contains 17 valid and 10 invalid archives.
- Added sample catalog/manifests and a reproducible corpus generator.
- Added named benchmark presets with explicit wall-clock deadlines and repeat summaries.
- Added native Windows setup/launcher scripts, automatic .env loading, PowerShell/
  Command Prompt examples and Windows/Linux CI configuration.
- Kept processing durations in real milliseconds; no duration-scaling setting added.
- The candidate API, attempt semantics and externally required state model are
  unchanged. No candidate executor solution is included.
