#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Iterable, List

import aiohttp
from aiohttp import ClientConnectorError, ClientError
from rich.console import Console
from rich.progress import Progress
from rich.table import Table
from urllib.parse import urlparse

console = Console()

# -------------------- Constants --------------------

TEST_PATH = "core/os/x86_64/core.db"
TEST_BYTES = 500_000
LATENCY_TRIES = 3
LATENCY_TIMEOUT = 5
SPEED_TIMEOUT = 10
SESSION_TOTAL_TIMEOUT = 15
DEFAULT_CONCURRENCY = 20

DEFAULT_ARCH_MIRRORLIST = "/etc/pacman.d/mirrorlist"
DEFAULT_CHAOTIC_MIRRORLIST = "/etc/pacman.d/chaotic-mirrorlist"


# -------------------- Data model --------------------

@dataclass(slots=True)
class MirrorResult:
    url: str
    latencies: List[float] = field(default_factory=list)
    speed: float = 0.0
    failures: int = 0
    valid: bool = False

    @property
    def latency(self) -> float:
        if not self.latencies:
            # large sentinel value for "worst latency"
            return 999.0
        return statistics.mean(self.latencies)

    @property
    def score(self) -> float:
        # higher speed, lower latency and fewer failures -> higher score
        return self.speed / (1.0 + self.latency + self.failures)


# -------------------- URL normalization & parsing helpers --------------------

def normalize_base_url(url: str) -> str | None:
    """
    Normalize a mirror base URL:

    - strip spaces
    - ensure it's http/https
    - keep only scheme + netloc
    - drop trailing slashes

    Returns None if URL is clearly invalid.
    """
    url = url.strip()
    if not url:
        return None
    first_http = url.find("http")
    if first_http > 0:
        url = url[first_http:].strip()

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    norm = f"{parsed.scheme}://{parsed.netloc}"
    return norm.rstrip("/")


def parse_arch_mirrors(path: str) -> list[str]:
    """
    Parse an Arch mirror input file that may contain:

      - plain URLs, e.g.:
          https://mirrors.mit.edu
          https://syd.mirror.rackspace.com

      - pacman-style lines, e.g.:
          Server = https://mirrors.mit.edu/archlinux/$repo/os/$arch

      - comments (# ...) and blank lines

    Returns a de-duplicated list of normalized base URLs,
    preserving the order of first appearance.
    """
    mirrors: list[str] = []
    seen: set[str] = set()

    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                base_url: str | None = None

                if line.startswith("Server"):
                    # pacman format:
                    # Server = https://mirror.example.com/archlinux/$repo/os/$arch
                    parts = line.split()
                    if len(parts) >= 3:
                        url_part = parts[2]
                        base_url = normalize_base_url(url_part)
                else:
                    # assume it's a raw URL
                    base_url = normalize_base_url(line)

                if base_url and base_url not in seen:
                    seen.add(base_url)
                    mirrors.append(base_url)

    except FileNotFoundError:
        console.print(f"[red]Arch mirror file not found at {path}[/red]")

    return mirrors


def deduplicate_results(results: list[MirrorResult]) -> list[MirrorResult]:
    """
    Safety net: ensure we don't have duplicate results by URL.
    (In theory parse_arch_mirrors already prevents duplicates,
    but this keeps things robust.)
    """
    seen: set[str] = set()
    unique: list[MirrorResult] = []
    for r in results:
        if r.url in seen:
            continue
        seen.add(r.url)
        unique.append(r)
    return unique


# -------------------- HTTP helpers --------------------

async def test_latency(session: aiohttp.ClientSession, url: str) -> float | None:
    """
    Perform a simple HTTP GET to measure latency.
    Returns latency in seconds on success, or None on error.
    """
    start = time.perf_counter()
    try:
        async with session.get(url, timeout=LATENCY_TIMEOUT) as resp:
            if resp.status == 200:
                return time.perf_counter() - start
            return None
    except (asyncio.TimeoutError, ClientConnectorError, ClientError):
        return None


async def test_speed(session: aiohttp.ClientSession, url: str) -> float:
    """
    Download a chunk of the file and return speed in bytes/sec.
    Returns 0 on failure or non-200 status.
    """
    start = time.perf_counter()
    try:
        async with session.get(url, timeout=SPEED_TIMEOUT) as resp:
            if resp.status != 200:
                return 0.0

            chunk = await resp.content.read(TEST_BYTES)
            if not chunk:
                return 0.0

            elapsed = time.perf_counter() - start
            if elapsed <= 0:
                return 0.0

            return len(chunk) / elapsed
    except (asyncio.TimeoutError, ClientConnectorError, ClientError):
        return 0.0


async def test_mirror(session: aiohttp.ClientSession, base_url: str) -> MirrorResult:
    """
    Test both latency and speed for a single Arch mirror.
    """
    result = MirrorResult(base_url)
    repo_url = f"{base_url}/archlinux/{TEST_PATH}"

    # Multiple latency measurements
    for _ in range(LATENCY_TRIES):
        lat = await test_latency(session, repo_url)
        if lat is not None:
            result.latencies.append(lat)
        else:
            result.failures += 1

    # Speed test
    speed = await test_speed(session, repo_url)
    if speed > 0:
        result.speed = speed
        result.valid = True

    return result


# -------------------- Arch mirror scan --------------------

async def run_scan(
    mirrors: Iterable[str],
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[MirrorResult]:
    """
    Scan a list of mirror base URLs with defined concurrency and return results.
    """
    mirrors = list(mirrors)
    if not mirrors:
        return []

    connector = aiohttp.TCPConnector(limit=concurrency)
    timeout = aiohttp.ClientTimeout(total=SESSION_TOTAL_TIMEOUT)

    results: list[MirrorResult] = []

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        with Progress() as progress:
            task_id = progress.add_task("Scanning Arch mirrors...", total=len(mirrors))
            coroutines = [test_mirror(session, m) for m in mirrors]

            for coro in asyncio.as_completed(coroutines):
                res = await coro
                results.append(res)
                progress.update(task_id, advance=1)

    return results


# -------------------- Table / file output --------------------

def build_table(results: list[MirrorResult], limit: int = 15) -> None:
    """
    Print a ranking table for the top N mirrors.
    """
    table = Table(title="Arch Mirror Ranking")

    table.add_column("Mirror")
    table.add_column("Latency (s)")
    table.add_column("Speed (KB/s)")
    table.add_column("Fails")
    table.add_column("Score")

    for r in results[:limit]:
        table.add_row(
            r.url,
            f"{r.latency:.3f}",
            f"{r.speed / 1024:.0f}",
            str(r.failures),
            f"{r.score:.2f}",
        )

    console.print(table)


def write_mirrorlist(
    results: list[MirrorResult],
    path: str = "mirrorlist.new",
) -> None:
    """
    Write a new Arch mirrorlist based on ranked mirrors.
    """
    lines = [
        f"Server = {r.url}/archlinux/$repo/os/$arch\n"
        for r in results
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    console.print(f"\n{path} written")


# -------------------- Chaotic-AUR scan --------------------

def extract_chaotic_mirrors(path: str) -> list[str]:
    """
    Parse a Chaotic-AUR style mirrorlist and extract unique base URLs.
    Expected line format: 'Server = https://example/$repo/$arch'
    """
    mirrors: list[str] = []
    seen: set[str] = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("Server"):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                url = parts[2]
                # normalize to base URL (drops /$repo/$arch, trailing slash, etc.)
                base = normalize_base_url(url)
                if not base:
                    continue
                if base in seen:
                    continue
                seen.add(base)
                mirrors.append(base)
    except FileNotFoundError:
        console.print(f"[red]Chaotic mirrorlist not found at {path}[/red]")
    return mirrors


async def is_chaotic_mirror_valid(
    session: aiohttp.ClientSession,
    base_url: str,
) -> bool:
    """
    Check if a Chaotic-AUR mirror is valid by fetching the main database file.
    """
    test_url = f"{base_url}/chaotic-aur/x86_64/chaotic-aur.db"
    try:
        async with session.get(test_url, timeout=LATENCY_TIMEOUT) as resp:
            return resp.status == 200
    except (asyncio.TimeoutError, ClientConnectorError, ClientError):
        return False


async def chaotic_scan(
    source_path: str,
    output_path: str = "chaotic-mirrorlist.new",
) -> None:
    """
    Scan Chaotic-AUR mirrors from a given mirrorlist file and write a new filtered one.
    """
    console.print("\nScanning Chaotic-AUR mirrors...")

    mirrors = extract_chaotic_mirrors(source_path)
    if not mirrors:
        console.print("[yellow]No Chaotic-AUR mirrors found to scan.[/yellow]")
        return

    async with aiohttp.ClientSession() as session:
        tasks = [is_chaotic_mirror_valid(session, m) for m in mirrors]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    valid = [m for m, ok in zip(mirrors, results) if ok]

    if not valid:
        console.print("[yellow]No valid Chaotic-AUR mirrors found.[/yellow]")
        return

    with open(output_path, "w", encoding="utf-8") as f:
        for m in valid:
            f.write(f"Server = {m}/$repo/$arch\n")

    console.print(f"{output_path} written")


# -------------------- CLI helpers --------------------

async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Arch mirror benchmark and Chaotic-AUR mirror checker",
    )

    parser.add_argument(
        "--mirrorlist",
        "-m",
        default=None,
        help=(
            "Path to Arch mirror file. "
            "Can contain plain URLs (one per line) and/or pacman-style lines like "
            "'Server = https://mirror.example.com/archlinux/$repo/os/$arch'. "
            "Comments (#) and blank lines are ignored."
        ),
    )
    parser.add_argument(
        "--chaotic-mirror",
        "-c",
        dest="chaotic_mirror",
        default=None,
        help=(
            "Path to Chaotic-AUR mirrorlist file to scan "
            "(e.g. /etc/pacman.d/chaotic-mirrorlist). "
            "If omitted, no Chaotic scan is performed."
        ),
    )
    parser.add_argument(
        "--concurrency",
        "-j",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Maximum concurrent connections for Arch scan (default: {DEFAULT_CONCURRENCY})",
    )

    args = parser.parse_args()

    # ---- Arch mirrors (from --mirrorlist) ----
    if args.mirrorlist:
        arch_mirrors = parse_arch_mirrors(args.mirrorlist)
        if not arch_mirrors:
            console.print(f"[red]No valid mirrors found in {args.mirrorlist}[/red]")
        else:
            results = await run_scan(arch_mirrors, args.concurrency)
            results = deduplicate_results(results)

            valid_results = [r for r in results if r.valid]
            valid_results.sort(key=lambda r: r.score, reverse=True)

            if not valid_results:
                console.print("[red]No valid Arch mirrors found.[/red]")
            else:
                build_table(valid_results)
                write_mirrorlist(valid_results, path="mirrorlist.new")
    else:
        console.print("[yellow]Arch scan skipped (no --mirrorlist provided).[/yellow]")

    # ---- Chaotic-AUR mirrors (from --chaotic-mirror) ----
    if args.chaotic_mirror:
        await chaotic_scan(
            source_path=args.chaotic_mirror,
            output_path="chaotic-mirrorlist.new",
        )
    else:
        console.print("[yellow]Chaotic-AUR scan skipped (no --chaotic-mirror provided).[/yellow]")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
