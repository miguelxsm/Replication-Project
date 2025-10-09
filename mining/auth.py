import os
import requests
from dotenv import load_dotenv
from dateutil import parser
from datetime import datetime, timezone
from collections import Counter
from tqdm import tqdm
import subprocess
import json


API_LINK = "https://api.github.com"

repos = [
"openstack/puppet-keystone",
"openstack/puppet-nova",
"openstack/puppet-neutron",
"openstack/puppet-glance",
"openstack/puppet-cinder",
"openstack/puppet-horizon",
"openstack/puppet-swift"
]

def authenticate():
    load_dotenv()
    TOKEN = os.getenv("GITHUB_TOKEN")
    return {"Authorization": f"token {TOKEN}"} if TOKEN else {}

def check_restriction_r2(HEADERS, repo_fullname: str, base_dir="repos_cache"):
    if not isinstance(repo_fullname, str):
        raise TypeError(f"repo_fullname debe ser str 'owner/repo', recibido: {type(repo_fullname)}")

    repo_url = f"https://github.com/{repo_fullname}.git"
    repo_dir = os.path.join(base_dir, repo_fullname.replace("/", "__"))

    os.makedirs(base_dir, exist_ok=True)

    if not os.path.isdir(repo_dir):
        # Clonado shallow para ir rápido; --quiet para menos ruido
        subprocess.run(
            ["git", "clone", "--depth", "1", "--no-tags", "--quiet", repo_url, repo_dir],
            check=True
        )

    # Conteo de archivos totales y .pp
    total = int(subprocess.check_output(
        ["bash", "-c", f"find '{repo_dir}' -type f | wc -l"], text=True
    ).strip() or 0)

    puppet = int(subprocess.check_output(
        ["bash", "-c", f"find '{repo_dir}' -type f -name '*.pp' | wc -l"], text=True
    ).strip() or 0)

    if total == 0:
        return False

    ratio = puppet / total
    return ratio >= 0.11



def check_restriction_r3_and_collect_commits(HEADERS, repo: str, per_page=100, months_window=24):
    def _first_day_utc(y, m): return datetime(y, m, 1, tzinfo=timezone.utc)

    def _add_months(y, m, k):
        idx = (y * 12 + (m - 1)) + k
        ny, nm = divmod(idx, 12)
        return ny, nm + 1

    # límites de ventana (meses completos, excluye el mes actual)
    now = datetime.now(timezone.utc)
    cy, cm = now.year, now.month
    end_y, end_m = _add_months(cy, cm, -1)                      # último mes incluido
    start_y, start_m = _add_months(end_y, end_m, -(months_window - 1))
    start_dt = _first_day_utc(start_y, start_m)
    until_dt = _first_day_utc(cy, cm)                           # primer día del mes actual (upper bound exclusivo)

    page = 1
    commits = []
    monthly = Counter()
    pbar = tqdm(desc=f"[R3] Commits {repo}", unit="page", leave=False)

    while True:
        r = requests.get(
            f"{API_LINK}/repos/{repo}/commits",
            headers=HEADERS,
            params={
                "per_page": per_page,
                "page": page,
                "since": start_dt.isoformat(),
                "until": until_dt.isoformat()
            }
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break

        for c in data:
            # usar fecha de integración en la rama; fallback a author si faltara
            dt = parser.isoparse(c["commit"]["committer"]["date"] or c["commit"]["author"]["date"])
            if not (start_dt <= dt < until_dt):
                continue
            y, m = dt.year, dt.month
            monthly[(y, m)] += 1
            commits.append({
                "sha": c["sha"],
                "date": dt.isoformat(),
                "message": c["commit"]["message"].split("\n")[0]
            })

        pbar.set_postfix({"pages": page, "commits": len(commits), "months": len(monthly)})
        pbar.update(1)
        page += 1

    pbar.close()

    total_commits = sum(monthly.values())
    avg_per_month = total_commits / months_window
    if avg_per_month < 2:
        print(f"{repo}: average {avg_per_month:.2f} commits/month (<2) in last {months_window} months (excluding current)")
        return False, None

    return True, commits


def navigate(HEADERS):
    info_repos = {}
    for repo in repos:
        print(f"Checking {repo}...")
        r = requests.get(f"{API_LINK}/repos/{repo}", headers=HEADERS)
        if r.status_code != 200:
            print(f"Error {r.status_code} en {repo}")
            info_repos[repo] = None
            continue

        repo_json = r.json()
        if repo_json.get("archived") or repo_json.get("disabled"):
            print(f"{repo} unavailabe (archived/disabled).")
            info_repos[repo] = None
            continue

        r2_ok = check_restriction_r2(HEADERS, repo)
        if not r2_ok:
            print(f"{repo}: <11% de .pp")
            info_repos[repo] = None
            continue

        r3_ok, commits = check_restriction_r3_and_collect_commits(HEADERS, repo)
        if not r3_ok:
            print(f"{repo}: does not satisfy R3")
            info_repos[repo] = None
            continue

        info_repos[repo] = commits

    return info_repos

if __name__ == "__main__":
    HEADERS = authenticate()
    info_repos = navigate(HEADERS)
    os.makedirs("output", exist_ok=True)

    # Guardar commits en un archivo JSON
    with open("output/mined_commits.json", "w", encoding="utf-8") as f:
        json.dump(info_repos, f, indent=2, ensure_ascii=False)

    print(" Commits guardados en output/mined_commits.json")