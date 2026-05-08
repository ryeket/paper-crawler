"""
低频持续论文采集器 — OpenAlex + Semantic Scholar + arXiv 三源轮询.
设计目标：安全限速范围内持续运行，随时可中断/续传。
输出到 ./essay_bank/

用法:
    python crawl_slow.py                  # 全量运行
    python crawl_slow.py --skip-oa        # 跳过 OpenAlex
    python crawl_slow.py --skip-arxiv     # 跳过 arXiv
"""
import os, re, sys, time, json
from datetime import datetime, timedelta
from pathlib import Path

from knowledge_source import (
    KnowledgeSource, sanitize_filename,
    load_done_set, append_metadata,
)
import knowledge_source

OUTPUT_BASE = Path(__file__).resolve().parent
knowledge_source.ESSAY_BANK = OUTPUT_BASE / "essay_bank"

import requests
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
# 限速参数
# ═══════════════════════════════════════════════════════════════

BATCH_SLEEP = 600  # 每批完成后休息 10 分钟
OA_REQ_INTERVAL = 2.0  # OpenAlex 每次 API 请求间隔 2s
S2_REQ_INTERVAL = 30.0  # S2 每次搜索间隔 30s（公共 API 极其严格）
ARXIV_REQ_INTERVAL = 3.0  # arXiv 每次请求间隔 3s
OA_BATCH_SIZE = 5  # OpenAlex 每批 5 个搜索词
S2_BATCH_SIZE = 2  # S2 每批 2 个搜索词
ARXIV_BATCH_SIZE = 3  # arXiv 每批 3 个搜索词

# ═══════════════════════════════════════════════════════════════
# 摘要质量验证
# ═══════════════════════════════════════════════════════════════

MIN_ABSTRACT_LEN = 150  # 短于 150 字视为不完整


def verify_abstract(abstract: str) -> str:
    """检查摘要质量。返回 'ok', 'short', 'truncated', 'empty'。"""
    text = (abstract or "").strip()
    if not text or len(text) < 10:
        return "empty"
    # 截断检测：非正常句子结尾
    truncated_endings = (",", ";", ":", "-", "—", "and", "or", "of", "to", "in", "for", "with", "the", "a")
    words = text.split()
    if words and words[-1].lower().rstrip(")") in truncated_endings:
        return "truncated"
    if len(text) < MIN_ABSTRACT_LEN:
        # 非常短的摘要，可能被截断
        if text[-1] not in ".!?\"'»」』”":
            return "truncated"
        return "short"
    if text[-1] not in ".!?\"'»」』”":
        return "truncated"
    return "ok"


# ═══════════════════════════════════════════════════════════════
# 搜索词条
# ═══════════════════════════════════════════════════════════════

OA_TERMS = [
    # 艺术 / 艺术史 (42)
    "contemporary art criticism aesthetics",
    "modern art history twentieth century",
    "Renaissance art history Italy",
    "Baroque art Caravaggio Rembrandt",
    "Impressionism painting Monet",
    "abstract expressionism American art",
    "Cubism Picasso modernism",
    "Chinese art history painting calligraphy",
    "Chinese landscape painting shanshui",
    "Buddhist art Dunhuang China",
    "art market auction economics",
    "feminist art history gender representation",
    "postcolonial art decolonizing museum",
    "conceptual art Duchamp readymade",
    "performance art body identity",
    "digital art new media technology",
    "photography art history theory",
    "museum studies curation exhibition",
    "art education pedagogy creativity",
    "street art graffiti Banksy",
    "art criticism theory Greenberg",
    "visual culture studies iconology",
    "art and politics activism",
    "aesthetics philosophy beauty Kant",
    "East Asian art Japan Korea",
    "Islamic art architecture history",
    "African art history diaspora",
    "pre-Columbian art Mesoamerica",
    "Rococo Neoclassicism eighteenth century",
    "Romanticism nineteenth century art",
    "Gothic art architecture medieval",
    "Byzantine art iconography",
    "ancient Greek art sculpture",
    "Roman art archaeology Pompeii",
    "art restoration conservation",
    "color theory painting technique",
    "sculpture history three-dimensional",
    "textile art fashion history",
    "ceramics pottery history art",
    "installation art site-specific",
    "video art film avant-garde",
    "sound art music experimental",
    # 神秘学 (28)
    "astrology history medieval Renaissance",
    "astrology astronomy scientific revolution",
    "tarot history symbolism playing cards",
    "esotericism Western tradition occult",
    "Hermeticism Renaissance philosophy",
    "Kabbalah Jewish mysticism history",
    "alchemy early modern science chemistry",
    "divination practices anthropology",
    "magic witchcraft history Europe",
    "Jung psychology archetypes symbolism",
    "mythology comparative religion",
    "ritual studies religious practice",
    "new age spirituality contemporary religion",
    "shamanism indigenous healing",
    "Gnosticism early Christianity",
    "Sufism Islamic mysticism",
    "Tantra Hindu Buddhist esoteric",
    "numerology Pythagorean symbolism",
    "parapsychology anomalous experience",
    "history of religion superstition",
    "folk belief vernacular religion",
    "secret societies fraternal organizations",
    "mysticism comparative study",
    "Rosicrucianism Freemasonry history",
    "occultism nineteenth century spiritualism",
    "anthropology of religion ritual",
    "sacred geometry symbolism architecture",
    "cosmology myth ancient astronomy",
    # 认知/学习 (40)
    "cognitive science embodied cognition",
    "metacognition learning strategies education",
    "cognitive bias heuristics judgment",
    "critical thinking education assessment",
    "systems thinking complexity theory",
    "design thinking creativity innovation",
    "mental models reasoning problem solving",
    "deliberate practice expertise development",
    "spaced repetition memory retention",
    "active recall retrieval practice learning",
    "behavioral economics prospect theory Kahneman",
    "Bayesian reasoning probability judgment",
    "creativity psychology divergent convergent",
    "growth mindset motivation education Dweck",
    "attention focus cognitive control distraction",
    "knowledge management organizational learning",
    "interdisciplinary learning education",
    "reading comprehension literacy education",
    "decision science judgment uncertainty",
    "cognitive neuroscience brain learning",
    "working memory executive function",
    "intelligence IQ cognitive ability",
    "problem solving insight creativity",
    "analogical reasoning transfer learning",
    "cognitive development Piaget Vygotsky",
    "social cognition theory of mind",
    "emotion cognition interaction",
    "consciousness philosophy mind neuroscience",
    "free energy principle predictive processing",
    "cognitive load theory instructional design",
    "self-regulated learning motivation",
    "epistemology belief formation knowledge",
    "numeracy statistical literacy",
    "wisdom intellectual humility",
    "cognitive aging elderly brain",
    "mindfulness meditation attention",
    "flow state optimal experience Csikszentmihalyi",
    "dual process theory intuition reasoning",
    "embodied cognition enactivism phenomenology",
    "4E cognition extended enactive embedded",
]

S2_TERMS = [
    "contemporary art theory aesthetics",
    "Chinese art history criticism",
    "Renaissance Baroque art history",
    "modern art modernism avant-garde",
    "digital art AI creativity",
    "history of astrology astronomy",
    "Jung archetypes psychology",
    "esotericism occultism Renaissance magic",
    "alchemy history of science",
    "metacognition learning strategies education",
    "cognitive bias decision making Kahneman",
    "critical thinking systems thinking",
    "creativity psychology innovation",
    "deliberate practice expertise performance",
    "knowledge management organizational learning",
]

ARXIV_TERMS = [
    ("contemporary art", "当代艺术"),
    ("generative art", "生成艺术"),
    ("AI creativity", "AI创造力"),
    ("computational creativity", "计算创意"),
    ("digital art aesthetics", "数字艺术美学"),
    ("artificial intelligence art", "人工智能艺术"),
    ("art history", "艺术史"),
    ("aesthetics philosophy", "美学哲学"),
    ("visual culture studies", "视觉文化"),
    ("cognitive science", "认知科学"),
    ("metacognition learning", "元认知学习"),
    ("cognitive bias decision", "认知偏差决策"),
    ("critical thinking education", "批判性思维教育"),
    ("mental models reasoning", "心智模型推理"),
    ("deliberate practice expertise", "刻意练习"),
    ("creativity psychology", "创造力心理学"),
    ("behavioral economics", "行为经济学"),
    ("attention focus cognitive", "注意力认知"),
    ("knowledge management", "知识管理"),
    ("predictive processing brain", "预测加工大脑"),
    ("embodied cognition", "具身认知"),
    ("Bayesian reasoning", "贝叶斯推理"),
    ("history of astronomy astrology", "天文学占星史"),
    ("Jung psychology archetypes", "荣格原型心理学"),
    ("history of magic science", "魔法与科学史"),
    ("divination anthropology", "占卜人类学"),
    ("esotericism Western history", "西方神秘学史"),
    ("alchemy early chemistry", "炼金术早期化学"),
    ("new age religion sociology", "新时代宗教社会学"),
    ("history of ideas intellectual", "思想史"),
]

# ═══════════════════════════════════════════════════════════════
# 进度持久化
# ═══════════════════════════════════════════════════════════════

PROGRESS_FILE = OUTPUT_BASE / "_collector_progress.json"


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"oa_done": [], "s2_done": [], "arxiv_done": []}


def save_progress(p: dict):
    PROGRESS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# OpenAlex Source
# ═══════════════════════════════════════════════════════════════

class OASource(KnowledgeSource):
    OA_CONTACT = "mailto:research@example.com"

    def __init__(self, terms: list[str]):
        self._terms = terms
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": f"mailto:{self.OA_CONTACT}"})
        proxy = (os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
                 or os.environ.get("http_proxy"))
        if proxy:
            self._session.proxies.update({"http": proxy, "https": proxy})
        self._rate_limited = False

    @property
    def source_name(self) -> str:
        return "openalex"

    def item_id(self, item: dict) -> str:
        return str(item.get("id", "")).split("/")[-1]

    def item_title(self, item: dict) -> str:
        return str(item.get("title", "untitled"))

    def item_date(self, item: dict) -> str:
        return str(item.get("publication_date", "1970-01-01"))[:10]

    def item_extra_tag(self, item: dict) -> str:
        loc = item.get("primary_location", {}) or {}
        source = (loc.get("source") or {}) if loc else {}
        name = source.get("display_name", "") if source else ""
        concepts = []
        for c in (item.get("concepts") or [])[:2]:
            if isinstance(c, dict):
                cn = c.get("display_name", "")
            else:
                cn = str(c)
            if cn:
                concepts.append(cn)
        tags = []
        if name:
            tags.append(str(name)[:30])
        if concepts:
            tags.append(", ".join(concepts)[:30])
        return " | ".join(tags)[:60]

    @staticmethod
    def _invert_abstract(inv: dict) -> str:
        if not inv:
            return ""
        max_pos = max(max(positions) for positions in inv.values())
        words = [""] * (max_pos + 1)
        for word, positions in inv.items():
            for pos in positions:
                if pos < len(words):
                    words[pos] = word
        return " ".join(words)

    def _api_get(self, url: str, retry: int = 0) -> dict | None:
        try:
            r = self._session.get(url, timeout=60)
            if r.status_code == 429:
                if not self._rate_limited:
                    print(f" [⚠ 429 限流]", end="", flush=True)
                    self._rate_limited = True
                if retry >= 2:
                    return None
                wait = 30 * (retry + 1)
                time.sleep(wait)
                return self._api_get(url, retry + 1)
            if r.status_code != 200:
                if retry < 2:
                    time.sleep(15)
                    return self._api_get(url, retry + 1)
                print(f" [{r.status_code}]", end="", flush=True)
                return None
            return r.json()
        except requests.exceptions.ConnectionError as e:
            if retry < 1:
                time.sleep(10)
                return self._api_get(url, retry + 1)
            print(f" [连接失败]", end="", flush=True)
            return None
        except Exception as e:
            if retry < 1:
                time.sleep(10)
                return self._api_get(url, retry + 1)
            print(f" [错误:{str(e)[:30]}]", end="", flush=True)
            return None

    def _search_one(self, kw: str) -> list[dict]:
        results_all = []
        cursor = "*"
        for page in range(5):
            time.sleep(OA_REQ_INTERVAL)
            url = (
                f"https://api.openalex.org/works"
                f"?search={requests.utils.quote(kw)}"
                f"&per_page=200&cursor={cursor}"
                f"&select=id,title,abstract_inverted_index,publication_date,"
                f"authorships,primary_location,cited_by_count,concepts,"
                f"open_access,type,language"
            )
            data = self._api_get(url)
            if not data:
                break

            for work in data.get("results", []):
                inv = work.get("abstract_inverted_index")
                abstract = self._invert_abstract(inv) if inv else ""
                authors = []
                for a in (work.get("authorships") or []):
                    au = a.get("author", {})
                    name = au.get("display_name", "")
                    if name:
                        authors.append(name)
                loc = work.get("primary_location") or {}
                source = (loc.get("source") or {}) if loc else {}
                oa_info = work.get("open_access", {}) or {}

                results_all.append({
                    "id": work.get("id", ""),
                    "title": work.get("title", "untitled"),
                    "abstract": abstract,
                    "authors": ", ".join(authors[:5]),
                    "publication_date": work.get("publication_date", "1970"),
                    "venue": source.get("display_name", "") if source else "",
                    "concepts": [(c.get("display_name", "")) for c in (work.get("concepts") or [])],
                    "cited_by_count": work.get("cited_by_count", 0),
                    "is_oa": oa_info.get("is_oa", False),
                    "oa_url": oa_info.get("oa_url", ""),
                    "type": work.get("type", ""),
                    "search_term": kw,
                })

            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                break

        return results_all

    def _enrich_abstracts(self, items: dict[str, dict]):
        """批量补全摘要 — 搜索接口截断到 ~250 词，filter-by-ID 接口返回全文。
        以 50 篇为一批请求，仅补全被截断或过短的摘要。"""
        need_enrich = {
            wid: item for wid, item in items.items()
            if verify_abstract(item.get("abstract", "")) != "ok"
        }
        if not need_enrich:
            return
        ids = list(need_enrich.keys())
        enriched = 0
        for batch_start in range(0, len(ids), 50):
            batch_ids = ids[batch_start:batch_start + 50]
            id_filter = "|".join(f"https://openalex.org/{oid}" for oid in batch_ids)
            url = (
                f"https://api.openalex.org/works"
                f"?filter=openalex_id:{requests.utils.quote(id_filter)}"
                f"&per_page=50"
                f"&select=id,abstract_inverted_index"
            )
            time.sleep(OA_REQ_INTERVAL)
            data = self._api_get(url)
            if not data:
                continue
            for work in data.get("results", []):
                wid = str(work.get("id", "")).split("/")[-1]
                inv = work.get("abstract_inverted_index")
                if inv and wid in items:
                    full = self._invert_abstract(inv)
                    if len(full) > len(items[wid].get("abstract", "")):
                        items[wid]["abstract"] = full
                        enriched += 1
        if enriched:
            print(f"    [补全摘要] {enriched}/{len(ids)} 篇")

    def fetch_batch(self, terms: list[str]) -> list[dict]:
        all_items: dict[str, dict] = {}
        for kw in terms:
            if self._rate_limited:
                print(f"    ⚠ 已触发限流，跳过剩余 {len(terms) - len(all_items)} 词条")
                break
            print(f"    {kw[:50]} ...", end="", flush=True)
            results = self._search_one(kw)
            print(f" {len(results)} 篇")
            for item in results:
                oid = self.item_id(item)
                if oid not in all_items:
                    all_items[oid] = item
        if all_items:
            self._enrich_abstracts(all_items)
        items = list(all_items.values())
        items.sort(key=lambda x: x.get("cited_by_count", 0) or 0, reverse=True)
        return items

    def fetch_list(self) -> list[dict]:
        # 由外部 batch 控制，不使用基类单次 fetch_list
        return []

    def download_one(self, item: dict) -> Path | None:
        tmp_dir = self.output_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{self.item_id(item)}.txt"
        concepts = ", ".join(str(c) for c in item.get("concepts", []))
        lines = [
            f"TITLE: {item.get('title', '')}",
            f"AUTHORS: {item.get('authors', '')}",
            f"DATE: {item.get('publication_date', 'N/A')}",
            f"JOURNAL: {item.get('venue', '')}",
            f"CONCEPTS: {concepts}",
            f"CITATIONS: {item.get('cited_by_count', 0)}",
            f"OPEN ACCESS: {item.get('oa_url', '')}",
            f"SEARCH: {item.get('search_term', '')}",
            "",
            f"ABSTRACT: {item.get('abstract', '')}",
        ]
        tmp_path.write_text("\n".join(lines), encoding="utf-8")
        return tmp_path

    def parse(self, raw_path: Path, item: dict) -> str:
        return raw_path.read_text(encoding="utf-8", errors="replace")

    def make_filename(self, item: dict) -> str:
        date = self.item_date(item)[:4] if self.item_date(item) else "1970"
        title = sanitize_filename(self.item_title(item))
        oid = sanitize_filename(self.item_id(item), 20)
        return f"{date} - {title} [{oid}].txt"

    def process_items(self, items: list[dict]):
        """直接内联处理——绕过基类 run() 的限制。"""
        done = load_done_set(self.metadata_path)
        success = fail = skip = 0
        total = len(items)
        q_empty = q_trunc = q_short = q_ok = 0

        for idx, item in enumerate(items, 1):
            iid = self.item_id(item)
            title_preview = sanitize_filename(self.item_title(item))[:50]

            if iid in done:
                skip += 1
                if idx % 500 == 0:
                    print(f"    [{idx}/{total}] ~ 跳过 (已下载)")
                continue

            try:
                raw_path = self.download_one(item)
            except Exception as e:
                fail += 1
                continue

            if raw_path is None:
                fail += 1
                continue

            try:
                text = self.parse(raw_path, item)
            except Exception:
                fail += 1
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            if not text.strip():
                skip += 1
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            # 质量验证
            q = verify_abstract(item.get("abstract", ""))
            if q == "empty":
                q_empty += 1
            elif q == "truncated":
                q_trunc += 1
            elif q == "short":
                q_short += 1
            else:
                q_ok += 1

            out_name = self.make_filename(item)
            out_path = self.output_dir / out_name
            out_path.write_text(out_name + "\n\n" + text, encoding="utf-8")

            try:
                raw_path.unlink(missing_ok=True)
            except Exception:
                pass

            append_metadata(self.metadata_path, {
                "id": iid, "title": self.item_title(item),
                "filename": out_name, "status": "success",
                "detail": out_name,
                "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            success += 1
            done.add(iid)

            if idx % 200 == 0:
                print(f"    [{idx}/{total}] +{success} x{fail} ~{skip}")

        parts = [f"+{success} x{fail} ~{skip}"]
        if q_trunc:
            parts.append(f"⚠截断{q_trunc}")
        if q_short:
            parts.append(f"短{q_short}")
        if q_empty:
            parts.append(f"空{q_empty}")
        parts.append(f"✓{q_ok}")
        print(f"    批次完成: {' | '.join(parts)}")
        return success, fail, skip


# ═══════════════════════════════════════════════════════════════
# Semantic Scholar Source
# ═══════════════════════════════════════════════════════════════

FIELDS = ("paperId,title,abstract,tldr,authors,year,publicationVenue,"
          "externalIds,citationCount,fieldsOfStudy,openAccessPdf")


class S2Source(KnowledgeSource):
    def __init__(self, terms: list[str]):
        self._terms = terms
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "LW-Slow-Collector/1.0"})
        proxy = (os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
                 or os.environ.get("http_proxy"))
        if proxy:
            self._session.proxies.update({"http": proxy, "https": proxy})
        self._rate_limited = False

    @property
    def source_name(self) -> str:
        return "semantic_scholar"

    def item_id(self, item: dict) -> str:
        return str(item.get("paperId", ""))

    def item_title(self, item: dict) -> str:
        return str(item.get("title", "untitled"))

    def item_date(self, item: dict) -> str:
        y = item.get("year")
        return f"{y}-01-01" if y else "1970-01-01"

    def item_extra_tag(self, item: dict) -> str:
        venue = item.get("publicationVenue") or item.get("journal", {})
        if isinstance(venue, dict):
            venue = venue.get("name", "")
        fos = item.get("fieldsOfStudy")
        if not isinstance(fos, list):
            fos = [str(fos)] if fos else []
        fos = fos[:2]
        tags = []
        if venue:
            tags.append(str(venue)[:30])
        if fos:
            tags.append(", ".join(str(f) for f in fos)[:30])
        return " | ".join(tags)[:60]

    def _api_get(self, path: str, params: dict = None, retry: int = 0) -> dict | None:
        try:
            r = self._session.get(f"https://api.semanticscholar.org/graph/v1{path}",
                                  params=params or {}, timeout=60)
            if r.status_code == 429:
                if not self._rate_limited:
                    print(f" [⚠ 429 限流]", end="", flush=True)
                    self._rate_limited = True
                if retry >= 2:
                    return None
                time.sleep(60 * (retry + 1))
                return self._api_get(path, params, retry + 1)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if retry < 2:
                time.sleep(30)
                return self._api_get(path, params, retry + 1)
            print(f" [错误:{str(e)[:30]}]", end="", flush=True)
            return None

    def _search_one(self, kw: str) -> list[dict]:
        time.sleep(S2_REQ_INTERVAL)
        params = {"query": kw, "limit": 100, "fields": FIELDS}
        data = self._api_get("/paper/search", params)
        if not data:
            return []

        articles = []
        for paper in data.get("data", []):
            abstract = paper.get("abstract", "") or ""
            tldr = (paper.get("tldr") or {}).get("text", "") if paper.get("tldr") else ""
            if not abstract and not tldr:
                continue
            ext_ids = paper.get("externalIds", {}) or {}
            oa_pdf = paper.get("openAccessPdf", {}) or {}
            articles.append({
                "paperId": paper.get("paperId", ""),
                "title": paper.get("title", "untitled"),
                "abstract": abstract,
                "tldr": tldr,
                "authors": ", ".join(a.get("name", "") for a in (paper.get("authors") or [])[:5]),
                "year": paper.get("year"),
                "venue": paper.get("publicationVenue", ""),
                "fieldsOfStudy": paper.get("fieldsOfStudy", []),
                "citationCount": paper.get("citationCount", 0),
                "doi": ext_ids.get("DOI", ""),
                "oa_url": oa_pdf.get("url", ""),
                "keyword": kw,
            })
        return articles

    def fetch_batch(self, terms: list[str]) -> list[dict]:
        all_items: dict[str, dict] = {}
        for kw in terms:
            if self._rate_limited:
                print(f"    ⚠ 已触发限流，跳过剩余 {len(terms) - len(all_items)} 词条")
                break
            print(f"    {kw[:50]} ...", end="", flush=True)
            results = self._search_one(kw)
            print(f" {len(results)} 篇")
            for item in results:
                pid = item["paperId"]
                if pid not in all_items:
                    all_items[pid] = item
        items = list(all_items.values())
        items.sort(key=lambda x: x.get("citationCount", 0) or 0, reverse=True)
        return items

    def fetch_list(self) -> list[dict]:
        return []

    def download_one(self, item: dict) -> Path | None:
        tmp_dir = self.output_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{self.item_id(item)}.txt"
        fos = item.get("fieldsOfStudy", [])
        if not isinstance(fos, list):
            fos = [str(fos)] if fos else []
        lines = [
            f"TITLE: {item.get('title', '')}",
            f"AUTHORS: {item.get('authors', '')}",
            f"YEAR: {item.get('year', 'N/A')}",
            f"JOURNAL: {item.get('venue', '')}",
            f"FIELDS: {', '.join(str(f) for f in fos)}",
            f"CITATIONS: {item.get('citationCount', 0)}",
            f"DOI: {item.get('doi', '')}",
            f"OA_URL: {item.get('oa_url', '')}",
            f"SEARCH: {item.get('keyword', '')}",
            "",
            f"TLDR: {item.get('tldr', '')}",
            "",
            f"ABSTRACT: {item.get('abstract', '')}",
        ]
        tmp_path.write_text("\n".join(lines), encoding="utf-8")
        return tmp_path

    def parse(self, raw_path: Path, item: dict) -> str:
        return raw_path.read_text(encoding="utf-8", errors="replace")

    def make_filename(self, item: dict) -> str:
        year = item.get("year", 1970)
        title = sanitize_filename(self.item_title(item))
        pid = sanitize_filename(self.item_id(item), 20)
        return f"{year} - {title} [{pid}].txt"

    def process_items(self, items: list[dict]):
        done = load_done_set(self.metadata_path)
        success = fail = skip = 0
        total = len(items)
        q_empty = q_trunc = q_short = q_ok = 0
        for idx, item in enumerate(items, 1):
            iid = self.item_id(item)
            if iid in done:
                skip += 1
                continue
            try:
                raw_path = self.download_one(item)
            except Exception:
                fail += 1
                continue
            if raw_path is None:
                fail += 1
                continue
            try:
                text = self.parse(raw_path, item)
            except Exception:
                fail += 1
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            if not text.strip():
                skip += 1
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            q = verify_abstract(item.get("abstract", "") or item.get("tldr", ""))
            if q == "empty": q_empty += 1
            elif q == "truncated": q_trunc += 1
            elif q == "short": q_short += 1
            else: q_ok += 1
            out_name = self.make_filename(item)
            (self.output_dir / out_name).write_text(out_name + "\n\n" + text, encoding="utf-8")
            try:
                raw_path.unlink(missing_ok=True)
            except Exception:
                pass
            append_metadata(self.metadata_path, {
                "id": iid, "title": self.item_title(item),
                "filename": out_name, "status": "success", "detail": out_name,
                "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            success += 1
            done.add(iid)
            if idx % 50 == 0:
                print(f"    [{idx}/{total}] +{success} x{fail} ~{skip}")
        parts = [f"+{success} x{fail} ~{skip}"]
        if q_trunc: parts.append(f"⚠截断{q_trunc}")
        if q_short: parts.append(f"短{q_short}")
        if q_empty: parts.append(f"空{q_empty}")
        parts.append(f"✓{q_ok}")
        print(f"    批次完成: {' | '.join(parts)}")
        return success, fail, skip


# ═══════════════════════════════════════════════════════════════
# arXiv Source
# ═══════════════════════════════════════════════════════════════

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _arxiv_opener():
    proxy = (os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
             or os.environ.get("http_proxy"))
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


class ArxivSource(KnowledgeSource):
    def __init__(self, keywords: list):
        self._keywords = keywords

    @property
    def source_name(self) -> str:
        return "arxiv"

    def item_id(self, item: dict) -> str:
        return str(item.get("arxiv_id", ""))

    def item_title(self, item: dict) -> str:
        return str(item.get("title", "untitled"))

    def item_date(self, item: dict) -> str:
        return str(item.get("submitted", "1970-01-01"))[:10]

    def item_extra_tag(self, item: dict) -> str:
        return str(item.get("categories", ""))[:40]

    def _api_get(self, params: dict, retry: int = 0) -> bytes | None:
        time.sleep(ARXIV_REQ_INTERVAL)
        url = f"https://export.arxiv.org/api/query?{urllib.parse.urlencode(params)}"
        try:
            opener = _arxiv_opener()
            r = opener.open(url, timeout=120)
            return r.read()
        except Exception as e:
            if retry < 2 and "429" in str(e):
                time.sleep(60)
                return self._api_get(params, retry + 1)
            return None

    def fetch_batch(self, terms: list) -> list[dict]:
        all_articles: dict[str, dict] = {}
        for kw, label in terms:
            print(f"    {label} ({kw[:35]}) ...", end="", flush=True)
            articles = []
            for start in range(0, 500, 100):
                params = {
                    "search_query": f"all:{kw}",
                    "start": start, "max_results": 100,
                    "sortBy": "submittedDate", "sortOrder": "descending",
                }
                xml_data = self._api_get(params)
                if not xml_data:
                    break
                try:
                    root = ET.fromstring(xml_data)
                except ET.ParseError:
                    break
                entries = root.findall(f"{ATOM_NS}entry")
                if not entries:
                    break
                for entry in entries:
                    def _text(tag):
                        el = entry.find(f"{ATOM_NS}{tag}")
                        return "".join(el.itertext()).strip() if el is not None else ""
                    aid = _text("id").split("/abs/")[-1].split("v")[0]
                    title = _text("title").replace("\n", " ").strip()
                    summary = _text("summary").strip()
                    submitted = _text("published")[:10] if _text("published") else ""
                    authors = []
                    for a in entry.findall(f"{ATOM_NS}author"):
                        n = a.find(f"{ATOM_NS}name")
                        if n is not None and n.text:
                            authors.append(n.text.strip())
                    cats = []
                    for c in entry.findall(f"{ARXIV_NS}primary_category"):
                        t = c.get("term", "")
                        if t:
                            cats.append(t)
                    articles.append({
                        "arxiv_id": aid, "title": title,
                        "authors": ", ".join(authors[:5]),
                        "summary": summary, "submitted": submitted,
                        "categories": ", ".join(cats),
                    })
                if len(entries) < 100:
                    break
            print(f" {len(articles)} 篇")
            for art in articles:
                aid = art["arxiv_id"]
                if aid not in all_articles:
                    all_articles[aid] = art
        items = list(all_articles.values())
        items.sort(key=lambda x: x.get("submitted", ""), reverse=True)
        return items

    def fetch_list(self) -> list[dict]:
        return []

    def download_one(self, item: dict) -> Path | None:
        tmp_dir = self.output_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{self.item_id(item)}.txt"
        tmp_path.write_text(
            f"TITLE: {item.get('title', '')}\n\n"
            f"AUTHORS: {item.get('authors', '')}\n\n"
            f"CATEGORIES: {item.get('categories', '')}\n\n"
            f"ABSTRACT: {item.get('summary', '')}",
            encoding="utf-8")
        return tmp_path

    def parse(self, raw_path: Path, item: dict) -> str:
        return raw_path.read_text(encoding="utf-8", errors="replace")

    def make_filename(self, item: dict) -> str:
        date = self.item_date(item)
        title = sanitize_filename(self.item_title(item))
        aid = sanitize_filename(self.item_id(item), 30)
        cats = sanitize_filename(self.item_extra_tag(item)[:50], 50) if self.item_extra_tag(item) else ""
        return f"{date} - {title} [{cats}] [{aid}].txt"

    def process_items(self, items: list[dict]):
        done = load_done_set(self.metadata_path)
        success = fail = skip = 0
        total = len(items)
        q_empty = q_trunc = q_short = q_ok = 0
        for idx, item in enumerate(items, 1):
            iid = self.item_id(item)
            if iid in done:
                skip += 1
                continue
            try:
                raw_path = self.download_one(item)
            except Exception:
                fail += 1
                continue
            if raw_path is None:
                fail += 1
                continue
            try:
                text = self.parse(raw_path, item)
            except Exception:
                fail += 1
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            if not text.strip():
                skip += 1
                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            q = verify_abstract(item.get("summary", ""))
            if q == "empty": q_empty += 1
            elif q == "truncated": q_trunc += 1
            elif q == "short": q_short += 1
            else: q_ok += 1
            out_name = self.make_filename(item)
            (self.output_dir / out_name).write_text(out_name + "\n\n" + text, encoding="utf-8")
            try:
                raw_path.unlink(missing_ok=True)
            except Exception:
                pass
            append_metadata(self.metadata_path, {
                "id": iid, "title": self.item_title(item),
                "filename": out_name, "status": "success", "detail": out_name,
                "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            success += 1
            done.add(iid)
            if idx % 200 == 0:
                print(f"    [{idx}/{total}] +{success} x{fail} ~{skip}")
        parts = [f"+{success} x{fail} ~{skip}"]
        if q_trunc: parts.append(f"⚠截断{q_trunc}")
        if q_short: parts.append(f"短{q_short}")
        if q_empty: parts.append(f"空{q_empty}")
        parts.append(f"✓{q_ok}")
        print(f"    批次完成: {' | '.join(parts)}")
        return success, fail, skip


# ═══════════════════════════════════════════════════════════════
# 主循环 — 三源轮流，分批 + 长冷却
# ═══════════════════════════════════════════════════════════════

def run_phase(source, name: str, all_terms: list, batch_size: int, progress_key: str):
    progress = load_progress()
    raw_done = progress.get(progress_key, [])
    # JSON 反序列化后 tuple → list，需要统一类型以支持 set
    if raw_done and isinstance(raw_done[0], list):
        raw_done = [tuple(x) for x in raw_done]
    done_terms = set(raw_done)
    pending = [t for t in all_terms if t not in done_terms]

    if not pending:
        print(f"  [{name}] 全部完成! ({len(all_terms)} 个词条)\n")
        return 0, 0, 0

    # 先探测第一个 batch 是否可用（仅对 API 限流做检测）
    probe = pending[:batch_size]
    print(f"  [{name}] 探测 API 可用性 ({len(probe)} 词条)...")
    probe_items = source.fetch_batch(probe)
    api_limited = hasattr(source, '_rate_limited') and source._rate_limited

    if api_limited:
        print(f"  [{name}] ⚠ API 429 限流，跳过本阶段（词条不标记完成）\n")
        return 0, 0, 0

    print(f"    探测: {len(probe_items)} 篇 (API 正常)")

    # API 正常则标记词条为已完成（无论是否有结果）
    for t in probe:
        done_terms.add(t)
    progress[progress_key] = list(done_terms)
    save_progress(progress)

    total_s, total_f, total_k = 0, 0, 0
    if probe_items:
        s, f, k = source.process_items(probe_items)
        total_s, total_f, total_k = s, f, k

    pending = [t for t in all_terms if t not in done_terms]
    if not pending:
        print(f"  [{name}] 全部完成! ({len(all_terms)} 个词条)\n")
        return total_s, total_f, total_k

    total_batches = 0
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start:batch_start + batch_size]
        print(f"  [{name}] 批次 {batch_start // batch_size + 1}/{(len(pending) + batch_size - 1) // batch_size}:"
              f" {len(batch)} 个词条 ({len(done_terms)}/{len(all_terms)} 已完成)")

        items = source.fetch_batch(batch)
        if items:
            print(f"    去重: {len(items)} 篇, 落盘中...")
            s, f, k = source.process_items(items)
            total_s += s
            total_f += f
            total_k += k
        else:
            print(f"    本批无结果")
            if hasattr(source, '_rate_limited') and source._rate_limited:
                print(f"  [{name}] ⚠ 检测到 429，停止本阶段")
                break

        # 标记完成
        for t in batch:
            done_terms.add(t)
        progress[progress_key] = list(done_terms)
        save_progress(progress)

        # 还剩未处理的才休息
        remaining = [t for t in all_terms if t not in done_terms]
        if remaining:
            print(f"    休息 {BATCH_SLEEP // 60} 分钟... (剩余 {len(remaining)} 词条)\n")
            time.sleep(BATCH_SLEEP)

    print(f"  [{name}] 阶段完成: +{total_s} x{total_f} ~{total_k}\n")
    return total_s, total_f, total_k


def main():
    import argparse
    parser = argparse.ArgumentParser(description="低频持续论文采集器")
    parser.add_argument("--skip-oa", action="store_true", help="跳过 OpenAlex")
    parser.add_argument("--skip-s2", action="store_true", help="跳过 Semantic Scholar")
    parser.add_argument("--skip-arxiv", action="store_true", help="跳过 arXiv")
    args = parser.parse_args()

    print("=" * 70)
    print(" 低频持续论文采集器")
    status_oa = "跳过" if args.skip_oa else f"{len(OA_TERMS)} 词条 / 批 {OA_BATCH_SIZE}"
    status_s2 = "跳过" if args.skip_s2 else f"{len(S2_TERMS)} 词条 / 批 {S2_BATCH_SIZE}"
    status_arx = "跳过" if args.skip_arxiv else f"{len(ARXIV_TERMS)} 词条 / 批 {ARXIV_BATCH_SIZE}"
    print(f" OpenAlex: {status_oa}")
    print(f" S2:       {status_s2}")
    print(f" arXiv:    {status_arx}")
    print(f" 批间休息: {BATCH_SLEEP // 60} 分钟")
    print(f" 输出:     {OUTPUT_BASE / 'essay_bank'}")
    print("=" * 70)
    print()

    # 加载进度
    progress = load_progress()
    for k, terms in [("oa_done", OA_TERMS), ("s2_done", S2_TERMS), ("arxiv_done", ARXIV_TERMS)]:
        done = len(progress.get(k, []))
        total = len(terms)
        if done > 0:
            print(f"  断点续传: {k} {done}/{total} 已完成")
    print()

    # 构建阶段列表
    phases = []
    if not args.skip_oa:
        phases.append(("OpenAlex", OASource(OA_TERMS), OA_TERMS, OA_BATCH_SIZE, "oa_done"))
    if not args.skip_s2:
        phases.append(("Semantic Scholar", S2Source(S2_TERMS), S2_TERMS, S2_BATCH_SIZE, "s2_done"))
    if not args.skip_arxiv:
        phases.append(("arXiv", ArxivSource(ARXIV_TERMS), ARXIV_TERMS, ARXIV_BATCH_SIZE, "arxiv_done"))

    if not phases:
        print("所有来源均已跳过，退出。")
        return

    grand_s, grand_f, grand_k = 0, 0, 0
    for name, source, terms, batch_size, prog_key in phases:
        print(f"── {name} ──")
        s, f, k = run_phase(source, name, terms, batch_size, prog_key)
        grand_s += s
        grand_f += f
        grand_k += k
        print()

    print("=" * 70)
    print(" 全部完成!")
    print(f"   总计: 成功 {grand_s} | 失败 {grand_f} | 跳过 {grand_k}")
    print(f"   输出: {OUTPUT_BASE / 'essay_bank'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
