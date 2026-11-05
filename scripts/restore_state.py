"""从同一分支的每日简报制品恢复状态，GitHub失败时直接退出。"""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlencode


def main():
    repository = os.environ["GH_REPO"]
    branch = os.environ["GITHUB_REF_NAME"]
    query = urlencode({"name": "pulse-state", "per_page": 100})
    pages = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/{repository}/actions/artifacts?{query}", "--paginate", "--slurp"],
            text=True,
            encoding="utf-8",
        )
    )
    for page in pages:
        for artifact in page["artifacts"]:
            run = artifact["workflow_run"]
            if artifact["expired"] or run["head_branch"] != branch:
                continue
            workflow = subprocess.check_output(
                ["gh", "api", f"repos/{repository}/actions/runs/{run['id']}", "--jq", ".path"],
                text=True,
                encoding="utf-8",
            ).strip()
            if workflow.split("@", 1)[0] != ".github/workflows/digest.yml":
                continue
            subprocess.run(
                [
                    "gh",
                    "run",
                    "download",
                    str(run["id"]),
                    "--repo",
                    repository,
                    "--name",
                    "pulse-state",
                    "--dir",
                    "data",
                ],
                check=True,
            )
            if not Path("data/pulse.db").is_file():
                raise FileNotFoundError("状态制品中缺少data/pulse.db，不创建空状态掩盖故障")
            print(f"已恢复分支{branch}的状态，来自运行{run['id']}")
            return
    print("未找到当前分支的有效状态制品；首次运行或保留期已过，将使用新状态库")


if __name__ == "__main__":
    main()
