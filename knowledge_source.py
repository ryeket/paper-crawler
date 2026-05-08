"""
知识源抽象框架 — 为论文采集器提供统一基类。

Usage:
    from knowledge_source import KnowledgeSource, sanitize_filename, load_done_set, append_metadata

    class MySource(KnowledgeSource):
        def fetch_list(self) -> list[dict]: ...
        def download_one(self, item: dict) -> Path | None: ...
        def parse(self, raw_path: Path, item: dict) -> str: ...
"""

import json
import hashlib
import re
import time
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional


# ── 输出路径（可被外部脚本覆盖）────────────────────

ESSAY_BANK = Path(__file__).resolve().parent / "essay_bank"


# ── JSON I/O ──────────────────────────────────────

def load_json(filepath: Path) -> Optional[dict | list]:
    if not filepath.exists():
        return None
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_json(filepath: Path, data):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(filepath)


# ── 文件名清理 ─────────────────────────────────────

def sanitize_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name


# ── 断点续传 ─────────────────────────────────────

def load_done_set(metadata_path: Path) -> set:
    records = load_json(metadata_path)
    if not isinstance(records, list):
        return set()
    return {r["id"] for r in records if r.get("status") == "success"}


def append_metadata(metadata_path: Path, record: dict):
    records = load_json(metadata_path)
    if not isinstance(records, list):
        records = []
    rid = record.get("id")
    replaced = False
    if rid:
        for i, r in enumerate(records):
            if r.get("id") == rid:
                records[i] = record
                replaced = True
                break
    if not replaced:
        records.append(record)
    save_json(metadata_path, records)


# ── 抽象基类 ─────────────────────────────────────

class KnowledgeSource(ABC):

    @property
    def source_name(self) -> str:
        return self.__class__.__name__.lower()

    @property
    def output_dir(self) -> Path:
        p = ESSAY_BANK / self.source_name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def metadata_path(self) -> Path:
        return self.output_dir / "_metadata.json"

    @property
    def done_set(self) -> set:
        return load_done_set(self.metadata_path)

    # ── 子类必须实现 ──

    @abstractmethod
    def fetch_list(self) -> list[dict]:
        ...

    @abstractmethod
    def download_one(self, item: dict) -> Optional[Path]:
        ...

    @abstractmethod
    def parse(self, raw_path: Path, item: dict) -> str:
        ...

    # ── 子类可选覆盖 ──

    def item_id(self, item: dict) -> str:
        return str(item.get("id", ""))

    def item_title(self, item: dict) -> str:
        return str(item.get("title", "untitled"))

    def item_date(self, item: dict) -> str:
        return str(item.get("pub_date", item.get("date", "1970-01-01")))[:10]

    def item_extra_tag(self, item: dict) -> str:
        return ""

    def make_filename(self, item: dict) -> str:
        date = self.item_date(item)
        title = sanitize_filename(self.item_title(item))
        tag = self.item_extra_tag(item)
        tag_part = f" - {sanitize_filename(tag, 40)}" if tag else ""
        src_id = sanitize_filename(self.item_id(item), 30)
        return f"{date} - {title}{tag_part} [{src_id}].txt"

    def run(self, max_items: int = 0, force: bool = False, workers: int = 1):
        print(f"{'='*60}")
        print(f" 知识源: {self.source_name}")
        print(f" 输出目录: {self.output_dir}")
        if workers > 1:
            print(f" 并行线程: {workers}")
        print(f"{'='*60}\n")

        print("获取条目列表...")
        items = self.fetch_list()
        if not items:
            print("没有找到任何条目。")
            return 0, 0, 0

        if max_items > 0:
            items = items[:max_items]

        done_set = set() if force else self.done_set
        if done_set:
            pending = [it for it in items if self.item_id(it) not in done_set]
            print(f"已下载: {len(done_set)} 个, 待处理: {len(pending)} 个\n")
        else:
            pending = items

        if not pending:
            print("所有条目已完成！")
            return 0, 0, 0

        total = len(items)
        success = fail = skip = 0
        _print_lock = threading.Lock()
        _meta_lock = threading.Lock()

        def process_one(idx, item):
            nonlocal success, fail, skip
            iid = self.item_id(item)
            title_preview = sanitize_filename(self.item_title(item))[:50]

            try:
                raw_path = self.download_one(item)
            except Exception as e:
                with _print_lock:
                    print(f"[{idx:4d}/{total}] x {title_preview:50s} [下载异常: {e}]")
                with _meta_lock:
                    append_metadata(self.metadata_path, {
                        "id": iid, "title": self.item_title(item),
                        "status": "failed", "detail": str(e)[:120],
                        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                return "fail"

            if raw_path is None:
                with _print_lock:
                    print(f"[{idx:4d}/{total}] x {title_preview:50s} [下载失败]")
                with _meta_lock:
                    append_metadata(self.metadata_path, {
                        "id": iid, "title": self.item_title(item),
                        "status": "failed", "detail": "download returned None",
                        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                return "fail"

            try:
                text = self.parse(raw_path, item)
            except Exception as e:
                with _print_lock:
                    print(f"[{idx:4d}/{total}] x {title_preview:50s} [解析异常: {e}]")
                with _meta_lock:
                    append_metadata(self.metadata_path, {
                        "id": iid, "title": self.item_title(item),
                        "status": "failed", "detail": f"parse error: {str(e)[:100]}",
                        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return "fail"

            if not text.strip():
                with _print_lock:
                    print(f"[{idx:4d}/{total}] ~ {title_preview:50s} [空内容]")
                with _meta_lock:
                    append_metadata(self.metadata_path, {
                        "id": iid, "title": self.item_title(item),
                        "status": "empty", "detail": "no text extracted",
                        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return "skip"

            out_name = self.make_filename(item)
            out_path = self.output_dir / out_name
            out_path.write_text(out_name + "\n\n" + text, encoding="utf-8")

            try:
                raw_path.unlink(missing_ok=True)
            except Exception:
                pass

            with _meta_lock:
                append_metadata(self.metadata_path, {
                    "id": iid, "title": self.item_title(item),
                    "filename": out_name,
                    "status": "success",
                    "detail": out_name,
                    "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

            tag = self.item_extra_tag(item)
            tag_str = f" [{sanitize_filename(tag, 30)}]" if tag else ""
            with _print_lock:
                print(f"[{idx:4d}/{total}] + {title_preview:50s}{tag_str}")
            return "success"

        if workers <= 1:
            for idx, item in enumerate(items, 1):
                iid = self.item_id(item)
                title_preview = sanitize_filename(self.item_title(item))[:50]
                if iid in done_set:
                    skip += 1
                    print(f"[{idx:4d}/{total}] ~ {title_preview:50s} [已下载]")
                    continue
                result = process_one(idx, item)
                if result == "success": success += 1
                elif result == "fail": fail += 1
                else: skip += 1
        else:
            indexed_pending = [(idx, item) for idx, item in enumerate(items, 1)
                               if self.item_id(item) not in done_set]
            skip_base = sum(1 for it in items if self.item_id(it) in done_set)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}
                for idx, item in indexed_pending:
                    future = executor.submit(process_one, idx, item)
                    futures[future] = (idx, item)

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result == "success": success += 1
                        elif result == "fail": fail += 1
                        else: skip += 1
                    except Exception as e:
                        idx, item = futures[future]
                        with _print_lock:
                            print(f"[{idx:4d}/{total}] x {sanitize_filename(self.item_title(item))[:50]} [线程异常: {e}]")
                        fail += 1

            skip = skip_base + (len(items) - len(indexed_pending) - success - fail)

        print()
        print(f"{'='*60}")
        print(f" 下载完成!")
        print(f"   总计: {total},  成功: {success},  失败: {fail},  跳过: {skip}")
        print(f"   输出目录: {self.output_dir}")
        print()

        return success, fail, skip
