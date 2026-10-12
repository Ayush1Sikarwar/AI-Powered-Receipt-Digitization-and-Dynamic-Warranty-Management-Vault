"""In-process Prometheus metrics (TSD §40 observability, §51 production).

Kept deliberately dependency-free (no prometheus_client): a request
counter + latency histogram maintained in the middleware, plus point-in-time
gauges (job queue, worker heartbeat, document counts) queried on scrape.
Exposed at ``/metrics`` only when ``metrics_enabled`` is true; the reverse
proxy never routes it to the public internet.
"""

import threading
import time

_LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000)


def _bucket_index(elapsed_ms: float) -> int:
    for i, edge in enumerate(_LATENCY_BUCKETS_MS):
        if elapsed_ms <= edge:
            return i
    return len(_LATENCY_BUCKETS_MS) - 1


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = {}  # (method, path_bucket, status_class)
        self._histograms: dict[str, list[int]] = {}  # path_bucket -> cumulative bucket counts

    def record_request(self, method: str, path: str, status_code: int, elapsed_ms: float) -> None:
        bucket = path_bucket(path)
        status_class = f"{status_code // 100}xx"
        with self._lock:
            key = (method, bucket, status_class)
            self._requests[key] = self._requests.get(key, 0) + 1
            hist = self._histograms.setdefault(bucket, [0] * len(_LATENCY_BUCKETS_MS))
            for i in range(_bucket_index(elapsed_ms) + 1):
                hist[i] += 1

    def render(self, boot_time: float, gauges: list[tuple[str, str, float | None]]) -> str:
        """Render the Prometheus text exposition format."""
        lines: list[str] = []
        lines.append("# HELP warrantyvault_requests_total HTTP requests by method, path, status class.")
        lines.append("# TYPE warrantyvault_requests_total counter")
        with self._lock:
            for (method, bucket, status_class), count in sorted(self._requests.items()):
                lines.append(f'warrantyvault_requests_total{{method="{method}",path="{bucket}",status="{status_class}"}} {count}')
            # hist[i] is cumulative ("le edge_i"), which is what Prometheus
            # histogram buckets require.
            for bucket, hist in sorted(self._histograms.items()):
                total = hist[-1]
                for i, edge in enumerate(_LATENCY_BUCKETS_MS):
                    lines.append(
                        f'warrantyvault_request_latency_ms_bucket{{path="{bucket}",le="{edge}"}} {hist[i]}'
                    )
                lines.append(f'warrantyvault_request_latency_ms_bucket{{path="{bucket}",le="+Inf"}} {total}')
                lines.append(f'warrantyvault_request_latency_ms_count{{path="{bucket}"}} {total}')
        uptime = round(time.time() - boot_time, 1)
        lines.append("# HELP warrantyvault_uptime_seconds Process uptime.")
        lines.append("# TYPE warrantyvault_uptime_seconds gauge")
        lines.append(f"warrantyvault_uptime_seconds {uptime}")
        for name, value, help_text in gauges:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            if value is None:
                lines.append(f"{name} NaN")
            else:
                lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"


def path_bucket(path: str) -> str:
    """Collapse paths into low-cardinality buckets (no user IDs in metrics)."""
    if path.startswith("/api/v1/"):
        seg = path[len("/api/v1/") :].split("?")[0].split("/")[0]
        return f"/api/v1/{seg}"
    if path.startswith("/api/"):
        return "/api/other"
    return path if path in ("/health", "/health/ready", "/metrics") else "/other"


metrics = Metrics()
