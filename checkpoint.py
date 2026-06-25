"""SQLite 断点续跑层

作用：
- 记录每个阶段的进度 (scan/images/videos/matching/report)
- 持久化图片哈希和视频关键帧
- 崩溃后重启可从断点继续，已处理的不重算
- 边处理边写入 duplicates 表，不攒内存
"""
import sqlite3
import json
import os
from config import RESULTS_DIR

os.makedirs(RESULTS_DIR, exist_ok=True)
DB_PATH = os.path.join(RESULTS_DIR, "checkpoint.db")


class Checkpoint:
    def __init__(self, db_path=DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS progress (
                phase      TEXT PRIMARY KEY,
                completed  INTEGER DEFAULT 0,
                total      INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS image_hashes (
                file_path  TEXT PRIMARY KEY,
                phash      TEXT NOT NULL,
                file_size  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_keyframes (
                file_path    TEXT PRIMARY KEY,
                hashes_json  TEXT NOT NULL,
                frame_count  INTEGER DEFAULT 0,
                duration     REAL DEFAULT 0,
                width        INTEGER DEFAULT 0,
                height       INTEGER DEFAULT 0,
                fps          REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS duplicates (
                group_id   INTEGER,
                file_path  TEXT,
                type       TEXT,
                similarity TEXT,
                reason     TEXT
            );
        """)
        self.conn.commit()

    # ═══ 进度读写 ═══

    def save_progress(self, phase, completed, total):
        self.conn.execute(
            "INSERT OR REPLACE INTO progress VALUES (?,?,?)",
            (phase, completed, total)
        )
        self.conn.commit()

    def load_progress(self, phase):
        row = self.conn.execute(
            "SELECT completed, total FROM progress WHERE phase=?", (phase,)
        ).fetchone()
        return row if row else (0, 0)

    def is_phase_done(self, phase):
        completed, total = self.load_progress(phase)
        return completed >= total and total > 0

    def set_folder(self, folder_path):
        """记录当前扫描的文件夹路径"""
        self.conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('folder', ?)", (folder_path,)
        )
        self.conn.commit()

    def get_folder(self):
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='folder'"
        ).fetchone()
        return row[0] if row else None

    def ensure_folder_match(self, folder_path):
        """检查文件夹是否匹配，不匹配则清空旧数据"""
        old = self.get_folder()
        if old and old != folder_path:
            self.cleanup()
            self.set_folder(folder_path)
            return False
        self.set_folder(folder_path)
        return True

    def reset_phase(self, phase):
        """重置某个阶段（文件变化时重新处理）"""
        self.conn.execute("DELETE FROM progress WHERE phase=?", (phase,))
        self.conn.commit()

    # ═══ 图片哈希 ═══

    def save_image_hash(self, path, phash_str, file_size):
        self.conn.execute(
            "INSERT OR REPLACE INTO image_hashes VALUES (?,?,?)",
            (path, phash_str, file_size)
        )

    def save_image_hashes_batch(self, rows):
        """批量写入 (path, phash_str, file_size) 列表"""
        self.conn.executemany(
            "INSERT OR REPLACE INTO image_hashes VALUES (?,?,?)", rows
        )
        self.conn.commit()

    def get_processed_images(self):
        return self.conn.execute(
            "SELECT file_path, phash, file_size FROM image_hashes"
        ).fetchall()

    def get_processed_image_paths(self):
        """返回已处理图片路径集合（用于增量跳过）"""
        rows = self.conn.execute(
            "SELECT file_path FROM image_hashes"
        ).fetchall()
        return {r[0] for r in rows}

    # ═══ 视频关键帧 ═══

    def save_video_keyframes(self, path, hashes_list, meta):
        self.conn.execute(
            "INSERT OR REPLACE INTO video_keyframes VALUES (?,?,?,?,?,?,?)",
            (
                path,
                json.dumps([str(h) for h in hashes_list]),
                len(hashes_list),
                meta.get("duration", 0) if meta else 0,
                meta.get("width", 0) if meta else 0,
                meta.get("height", 0) if meta else 0,
                meta.get("fps", 0) if meta else 0,
            )
        )
        self.conn.commit()

    def get_processed_videos(self):
        """返回已处理视频的 (path, [hash_strs], meta_dict)"""
        rows = self.conn.execute(
            "SELECT file_path, hashes_json, frame_count, duration, width, height, fps "
            "FROM video_keyframes"
        ).fetchall()
        result = {}
        for r in rows:
            hashes = []
            try:
                hashes = json.loads(r[1])
            except Exception:
                pass
            result[r[0]] = {
                "hashes": hashes,
                "frame_count": r[2],
                "meta": {
                    "duration": r[3],
                    "width": r[4],
                    "height": r[5],
                    "fps": r[6],
                }
            }
        return result

    def get_processed_video_paths(self):
        rows = self.conn.execute(
            "SELECT file_path FROM video_keyframes"
        ).fetchall()
        return {r[0] for r in rows}

    # ═══ 重复结果 ═══

    def save_duplicate_group(self, group_id, group_dict):
        rows = [
            (group_id, f, group_dict["type"],
             group_dict.get("similarity", ""),
             group_dict.get("reason", ""))
            for f in group_dict["files"]
        ]
        self.conn.executemany(
            "INSERT INTO duplicates VALUES (?,?,?,?,?)", rows
        )
        self.conn.commit()

    def get_duplicates(self):
        rows = self.conn.execute(
            "SELECT group_id, file_path, type, similarity, reason "
            "FROM duplicates ORDER BY group_id"
        ).fetchall()
        groups = {}
        for r in rows:
            gid = r[0]
            if gid not in groups:
                groups[gid] = {
                    "type": r[2],
                    "similarity": r[3],
                    "reason": r[4],
                    "files": [],
                    "count": 0,
                }
            groups[gid]["files"].append(r[1])
            groups[gid]["count"] += 1
        return list(groups.values())

    def clear_duplicates(self):
        self.conn.execute("DELETE FROM duplicates")
        self.conn.commit()

    # ═══ 状态查询 ═══

    def get_status(self):
        """返回当前断点状态摘要"""
        phases = ["scan", "images", "videos", "matching", "report"]
        status = {}
        for p in phases:
            completed, total = self.load_progress(p)
            status[p] = {"completed": completed, "total": total, "done": completed >= total and total > 0}
        img_count = self.conn.execute("SELECT COUNT(*) FROM image_hashes").fetchone()[0]
        vid_count = self.conn.execute("SELECT COUNT(*) FROM video_keyframes").fetchone()[0]
        dup_count = self.conn.execute("SELECT COUNT(DISTINCT group_id) FROM duplicates").fetchone()[0]
        status["_counts"] = {"images": img_count, "videos": vid_count, "duplicates": dup_count}
        return status

    def cleanup(self):
        """清空所有 checkpoint 数据（用于结果输出后清理）"""
        self.conn.executescript("""
            DELETE FROM progress;
            DELETE FROM image_hashes;
            DELETE FROM video_keyframes;
            DELETE FROM duplicates;
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()
