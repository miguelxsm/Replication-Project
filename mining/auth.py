import os
import requests
from dotenv import load_dotenv
from dateutil import parser
from datetime import datetime, timezone

API_LINK = "https://api.github.com"

repos = [
    ... # add repos
]

def authenticate():
    load_dotenv()
    TOKEN = os.getenv("GITHUB_TOKEN")
    return {"Authorization": f"token {TOKEN}"} if TOKEN else {}

def check_restriction_r2(HEADERS, repo: str):
    # default_branch
    repo_info = requests.get(f"{API_LINK}/repos/{repo}", headers=HEADERS)
    repo_info.raise_for_status()
    default_branch = repo_info.json().get("default_branch", "main")

    r = requests.get(f"{API_LINK}/repos/{repo}/branches/{default_branch}", headers=HEADERS)
    r.raise_for_status()
    commit_sha = r.json()["commit"]["sha"]

    rc = requests.get(f"{API_LINK}/repos/{repo}/commits/{commit_sha}", headers=HEADERS)
    rc.raise_for_status()
    tree_sha = rc.json()["commit"]["tree"]["sha"]

    rt = requests.get(f"{API_LINK}/repos/{repo}/git/trees/{tree_sha}", params={"recursive": "1"}, headers=HEADERS)
    rt.raise_for_status()
    tree = rt.json().get("tree", [])

    files = [t for t in tree if t.get("type") == "blob"]
    if not files:
        return False
    pp_files = [f for f in files if f.get("path","").endswith(".pp")]
    ratio = len(pp_files) / len(files)
    return ratio >= 0.11

def check_restriction_r3_and_collect_commits(HEADERS, repo: str, per_page=100):
    def prev_month(y, m): return (y-1, 12) if m == 1 else (y, m-1)

    now = datetime.now(timezone.utc)
    current_y, current_m = now.year, now.month
    expect_y, expect_m = prev_month(current_y, current_m)

    page = 1
    commits_in_month = 0
    have_any_month = False
    commits = []

    while True:
        r = requests.get(f"{API_LINK}/repos/{repo}/commits",
                         headers=HEADERS, params={"per_page": per_page, "page": page})
        r.raise_for_status()
        data = r.json()
        if not data:
            break

        for c in data:
            dt = parser.isoparse(
            c["commit"]["committer"]["date"] 
            or c["commit"]["author"]["date"]  # fallback por si no hay committer
        )
            y, m = dt.year, dt.month
            if (y, m) == (current_y, current_m):
                continue
            have_any_month = True

            commits.append({"sha": c["sha"], "date": dt.isoformat(),
                            "message": c["commit"]["message"].split("\n")[0]})

            if (y, m) == (expect_y, expect_m):
                commits_in_month += 1
            else:
                if commits_in_month < 2:
                    print(f"{repo} does not have enough commits on {expect_m}/{expect_y}")
                    return False, None
                expect_y, expect_m = prev_month(expect_y, expect_m)
                commits_in_month = 1

        page += 1

    if have_any_month and commits_in_month < 2:
        return False, None
    if not have_any_month:
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
    print("Resumen:")
    for repo, commits in info_repos.items():
        print(repo, "->", None if commits is None else f"{len(commits)} commits recopilados")
