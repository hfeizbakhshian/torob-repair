"""Start the local stack: optionally the database, then the API, the worker and the web UI.

Deliberately does nothing destructive: it installs nothing, runs no migration and deletes
no data. The README gives the first-run order. Works on Linux and Windows, stops every
child process on exit, checks each service is actually answering, and prints a clear
message when one fails.

    uv run --project backend python scripts/dev.py --with-db
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"
IS_WINDOWS = os.name == "nt"

API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = os.environ.get("API_PORT", "8000")
WEB_PORT = os.environ.get("WEB_PORT", "3000")
API_HEALTH = f"http://{API_HOST}:{API_PORT}/api/health"
WEB_URL = f"http://127.0.0.1:{WEB_PORT}"


class Service:
    def __init__(self, name: str, command: list[str], cwd: Path) -> None:
        self.name = name
        self.command = command
        self.cwd = cwd
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        executable = shutil.which(self.command[0])
        if executable is None:
            raise SystemExit(
                f"[{self.name}] دستور «{self.command[0]}» پیدا نشد. "
                "پیش‌نیازهای README را نصب کنید."
            )
        print(f"▶ {self.name}: {' '.join(self.command)}")
        # A new process group lets one signal stop the whole tree on either platform.
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        self.process = subprocess.Popen(
            [executable, *self.command[1:]],
            cwd=self.cwd,
            creationflags=creation_flags,
            start_new_session=not IS_WINDOWS,
        )

    def poll_failure(self) -> str | None:
        if self.process is None:
            return None
        code = self.process.poll()
        if code is None or code == 0:
            return None
        return f"[{self.name}] با کد {code} متوقف شد."

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        print(f"■ توقف {self.name}")
        try:
            if IS_WINDOWS:
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


def wait_for_http(url: str, *, name: str, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    # A local service is never reached through a proxy, whatever the shell exports.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:
                if response.status < 500:
                    print(f"✓ {name} آماده است: {url}")
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1)
    print(f"✗ {name} در مهلت مقرر پاسخ نداد: {url}", file=sys.stderr)
    return False


def port_in_use(host: str, port: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, int(port))) == 0


def check_ports_free(*, with_web: bool) -> bool:
    """A stale process on our port would answer the health check in our place."""
    busy = [(API_HOST, API_PORT, "API")]
    if with_web:
        busy.append(("127.0.0.1", WEB_PORT, "رابط وب"))
    taken = [(port, name) for host, port, name in busy if port_in_use(host, port)]
    for port, name in taken:
        print(
            f"✗ پورت {port} ({name}) از قبل اشغال است. احتمالاً اجرای قبلی هنوز زنده است؛ "
            "آن را ببندید یا با API_PORT/WEB_PORT پورت دیگری بدهید.",
            file=sys.stderr,
        )
    return not taken


def start_database() -> None:
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit(
            "برای --with-db به Docker نیاز است. یا Docker را نصب کنید، "
            "یا بدون این گزینه اجرا کنید و DATABASE_URL را به پایگاه موجود بدهید."
        )
    print("▶ راه‌اندازی پایگاه دادهٔ محلی")
    result = subprocess.run(
        [docker, "compose", "up", "-d", "db"], cwd=REPO_ROOT, check=False
    )
    if result.returncode != 0:
        raise SystemExit("راه‌اندازی پایگاه داده ناموفق بود.")


def main() -> int:
    parser = argparse.ArgumentParser(description="اجرای هماهنگ سرویس‌های محلی")
    parser.add_argument(
        "--with-db",
        action="store_true",
        help="پایگاه دادهٔ محلی را هم با Docker Compose بالا می‌آورد.",
    )
    parser.add_argument(
        "--no-web", action="store_true", help="فقط API و worker را اجرا می‌کند."
    )
    args = parser.parse_args()

    if not check_ports_free(with_web=not args.no_web):
        return 1

    if args.with_db:
        start_database()

    services = [
        Service(
            "api",
            ["uv", "run", "uvicorn", "app.main:app", "--host", API_HOST, "--port", API_PORT],
            BACKEND,
        ),
        Service("worker", ["uv", "run", "python", "-m", "app.worker"], BACKEND),
    ]
    if not args.no_web:
        services.append(
            Service("web", ["npm", "run", "dev", "--", "--port", WEB_PORT], FRONTEND)
        )

    try:
        services[0].start()
        if not wait_for_http(API_HEALTH, name="API") or services[0].poll_failure():
            print(
                "راهنمایی: آیا migration اجرا شده است؟ "
                "`uv run alembic upgrade head` را در backend/ اجرا کنید.",
                file=sys.stderr,
            )
            return 1

        for service in services[1:]:
            service.start()

        if not args.no_web and not wait_for_http(WEB_URL, name="رابط وب", timeout=120):
            return 1

        print("\nهمهٔ سرویس‌ها اجرا شدند. برای توقف Ctrl+C بزنید.")
        print(f"  رابط: {WEB_URL}")
        print(f"  API:  http://{API_HOST}:{API_PORT}/api/docs")

        while True:
            for service in services:
                failure = service.poll_failure()
                if failure:
                    print(failure, file=sys.stderr)
                    return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nدریافت Ctrl+C — در حال توقف سرویس‌ها…")
        return 0
    finally:
        for service in reversed(services):
            service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
