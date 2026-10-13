"""复制固定训练快照到本地运行目录；绝不覆盖已有训练成果。"""
from pathlib import Path
import shutil


def restore():
    base = Path(__file__).resolve().parent
    destination = base / 'chroma_db'
    if destination.exists():
        raise SystemExit('chroma_db 已存在，未覆盖；如需重置，请先自行备份并移走。')
    shutil.copytree(base / 'examples' / 'chroma_db', destination)
    print('训练快照已恢复到 chroma_db；请保持 CHROMA_PATH=./chroma_db。')


if __name__ == '__main__':
    restore()
