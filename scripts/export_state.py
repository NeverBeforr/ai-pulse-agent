"""只导出恢复任务所需状态，排除文章正文与模型原文证据。"""

from pathlib import Path

from ai_pulse.config import load_config
from ai_pulse.storage import Store

if __name__ == "__main__":
    config = load_config("pulse.yaml", offline=True)
    path = Path(config.state.path)
    if path.exists():
        Store(path.parent, database_path=path).export_state(Path("data/checkpoint/pulse.db"))
        print("已导出去除原文的状态快照")
    else:
        print("本轮没有状态数据库，无需导出")
