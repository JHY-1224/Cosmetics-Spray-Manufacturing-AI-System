"""Measure API behavior against a configured local software validation instance."""

import argparse
import concurrent.futures
import json
import platform
import statistics
import time
from pathlib import Path

import httpx
import psutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--credentials", type=Path, default=Path("var/demo-admin.json"), help="授权的隔离验证管理员凭据文件；不写入报告")
    parser.add_argument("--output", type=Path, default=Path("reports/performance.json"))
    parser.add_argument("--server-pid", type=int, help="已核对的本机验证服务PID，仅测量资源，不发送信号")
    parser.add_argument("--paths", nargs="+", default=["/api/cases", "/api/statistics", "/api/operations", "/api/masters"])
    parser.add_argument('--scenario', type=Path, help='客户待确认的容量假设JSON；不等于性能或SLA承诺')
    args = parser.parse_args()
    if not 1 <= args.requests <= 100000 or not 1 <= args.concurrency <= 64:
        parser.error('requests为1..100000，concurrency为1..64')
    if any(not path.startswith("/api/") or "://" in path for path in args.paths):
        parser.error("paths仅支持同一验证实例的/api/只读路径")
    if args.output.exists():
        parser.error("报告已存在，请指定新output，避免覆盖历史证据")
    credentials = json.loads(args.credentials.read_text())
    with httpx.Client(base_url=args.url, timeout=10) as client:
        response = client.post("/api/auth/login", json=credentials)
        response.raise_for_status()
        paths = args.paths
        dataset = {'cases': client.get('/api/cases?page=1&page_size=1').json()['total'],
                   'inspection_records': client.get('/api/statistics').json()['record_count'],
                   'masters': len(client.get('/api/masters').json())}
        if any(path.startswith('/api/industrial') or '/industrial-' in path for path in paths):
            dataset['industrial_records'] = {}
            for resource in ('boms', 'orders', 'inventory', 'cost-reports', 'digital-tasks', 'business-commands'):
                response = client.get('/api/records/industrial-' + resource, params={'page': 1, 'page_size': 1})
                response.raise_for_status()
                dataset['industrial_records'][resource] = response.json()['total']
        from urllib.parse import urlparse

        port = str(urlparse(args.url).port or 8000)
        server = psutil.Process(args.server_pid) if args.server_pid else None
        for process in [] if server else psutil.process_iter(["cmdline"]):
            try:
                command = process.info["cmdline"] or []
                if (
                    "quality.app:app" in command
                    and "--port" in command
                    and command[command.index("--port") + 1] == port
                ):
                    server = process
                    break
            except (psutil.AccessDenied, psutil.NoSuchProcess, IndexError):
                continue
        rss_before = server.memory_info().rss if server else None
        cpu_before = sum(server.cpu_times()[:2]) if server else None

        def request(index):
            started = time.perf_counter()
            r = client.get(paths[index % len(paths)])
            return (time.perf_counter() - started) * 1000, r.status_code

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            values = list(pool.map(request, range(args.requests)))
        duration = time.perf_counter() - started
        latencies = sorted(v[0] for v in values)
        result = {
            "environment": {
                "os": platform.platform(),
                "cpu": platform.processor(),
                "logical_cpus": psutil.cpu_count(),
                "memory_bytes": psutil.virtual_memory().total,
                "python": platform.python_version(),
            },
            "scenario": "local synthetic API reads; no vision/model inference or production quality evaluation",
            'dataset': dataset,
            'capacity_assumptions': json.loads(args.scenario.read_text()) if args.scenario else None,
            'customer_acceptance_targets': 'not_confirmed',
            "requests": args.requests,
            "paths": paths,
            "concurrency": args.concurrency,
            "duration_seconds": duration,
            "requests_per_second": args.requests / duration,
            "latency_ms": {
                "p50": statistics.median(latencies),
                "p95": latencies[max(0, int(len(latencies) * 0.95) - 1)],
                "max": max(latencies),
            },
            "http_errors": sum(s != 200 for _, s in values),
            "client_process_rss": psutil.Process().memory_info().rss,
            "server_rss_before": rss_before,
            "server_rss_after": server.memory_info().rss if server else None,
            "server_cpu_seconds": sum(server.cpu_times()[:2]) - cpu_before if server else None,
            "client_cpu_seconds": sum(psutil.Process().cpu_times()[:2]),
            "limitations": [
                "SQLite local demo",
                "client and server on same laptop",
                "small synthetic dataset",
                "not a load capacity or availability guarantee",
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
