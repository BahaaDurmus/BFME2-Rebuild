"""Read-only GitHub snapshot for ci/maint/ via GraphQL + REST."""
import json
import subprocess

REPO = "Open-BFME/BFME2-Rebuild"
OWNER, NAME = REPO.split("/", 1)
MAX_PR_PAGES = 20

OPEN_PULLS_QUERY = """query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { oid } }
    pullRequests(states: OPEN, baseRefName: "main", first: 50, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        isDraft
        maintainerCanModify
        baseRefOid
        headRefOid
        author { login }
        files(first: 100) {
          pageInfo { hasNextPage }
          nodes { path }
        }
        commits(last: 1) {
          nodes {
            commit {
              oid
              status {
                contexts {
                  context
                  state
                  description
                  createdAt
                  creator { login __typename }
                }
              }
            }
          }
        }
      }
    }
  }
}"""


class GhApiError(RuntimeError):
    def __init__(self, message, *, definite=False):
        super().__init__(message)
        self.definite = definite


class GhClient:
    """Minimal GitHub client using gh CLI (reads always allowed; writes blocked unless live=True)."""

    def __init__(self, repo=REPO, *, live=False):
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self.live = bool(live)
        self.writes = []

    def graphql(self, query, variables):
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in variables.items():
            if v is not None:
                cmd.extend(["-f", f"{k}={v}"])
        out = subprocess.check_output(cmd, text=True, timeout=60)
        data = json.loads(out)
        if data.get("errors"):
            raise RuntimeError(f"GraphQL error: {data['errors']}")
        return data["data"]

    def get(self, path):
        out = subprocess.check_output(["gh", "api", f"repos/{self.repo}/{path}"], text=True, timeout=60)
        return json.loads(out)

    def _write(self, method, path, payload=None):
        if not self.live:
            raise RuntimeError(f"Write refused in dry-run mode: {method} {path}")
        self.writes.append((method, path, payload))
        cmd = ["gh", "api", "--method", method, f"repos/{self.repo}/{path}"]
        try:
            if payload is not None:
                p = subprocess.run(cmd + ["--input", "-"], input=json.dumps(payload), text=True, capture_output=True, timeout=60)
            else:
                p = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise GhApiError(f"{method} {path} timed out (ambiguous outcome)", definite=False) from exc
        if p.returncode != 0:
            err = p.stderr.strip()
            # HTTP 404/409/422 are definite rejections; 5xx/network errors are ambiguous
            definite = any(code in err for code in ("HTTP 400", "HTTP 403", "HTTP 404", "HTTP 409", "HTTP 422"))
            raise GhApiError(f"{method} {path} failed ({p.returncode}): {err}", definite=definite)
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def post(self, path, payload=None):
        return self._write("POST", path, payload)

    def put(self, path, payload=None):
        return self._write("PUT", path, payload)


def fetch_snapshot(client):
    """Fetch current main SHA and all open PRs with authenticated commit status metadata."""
    pulls = []
    main_sha = ""
    cursor = None
    pulls_truncated = False
    for _ in range(MAX_PR_PAGES):
        data = client.graphql(OPEN_PULLS_QUERY, {"owner": client.owner, "name": client.name, "cursor": cursor})
        repo = data["repository"]
        main_sha = (repo.get("defaultBranchRef") or {}).get("target", {}).get("oid") or main_sha
        conn = repo["pullRequests"]
        for node in conn.get("nodes") or ():
            commits = (node.get("commits") or {}).get("nodes") or []
            commit = (commits[-1] or {}).get("commit") if commits else {}
            contexts = ((commit or {}).get("status") or {}).get("contexts") or []
            files_conn = node.get("files") or {}
            files = [f["path"] for f in (files_conn.get("nodes") or ()) if f and f.get("path")]
            files_truncated = bool((files_conn.get("pageInfo") or {}).get("hasNextPage"))
            pulls.append({
                "number": int(node["number"]),
                "title": node.get("title") or "",
                "draft": bool(node.get("isDraft")),
                "maintainer_can_modify": bool(node.get("maintainerCanModify")),
                "base": node.get("baseRefOid") or "",
                "head": node.get("headRefOid") or "",
                "status_commit_oid": (commit or {}).get("oid") or "",
                "author": ((node.get("author") or {}).get("login") or ""),
                "files": files,
                "files_truncated": files_truncated,
                "statuses": [
                    {
                        "context": c.get("context") or "",
                        "state": (c.get("state") or "").lower(),
                        "description": c.get("description") or "",
                        "created_at": c.get("createdAt") or "",
                        "creator_login": ((c.get("creator") or {}).get("login") or ""),
                        "creator_type": ((c.get("creator") or {}).get("__typename") or ""),
                    }
                    for c in contexts
                ],
            })
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            pulls_truncated = False
            break
        if not page.get("endCursor"):
            pulls_truncated = True
            break
        cursor = page["endCursor"]
        pulls_truncated = True
    return {"main": main_sha, "pulls": pulls, "pulls_truncated": pulls_truncated}
