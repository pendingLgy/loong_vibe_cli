r"""独立调试脚本：查询 LangGraph Checkpointer 在 PostgreSQL 中的持久化数据。

用法（参数通过函数参数传入，不使用命令行参数）：
    from scripts.inspect_checkpoint import inspect_table, inspect_table_json

    # 方式一：返回可 JSON 序列化的 dict
    result = inspect_table("checkpoints", thread_id="123025", limit=50)

    # 方式二：直接返回 JSON 字符串
    print(inspect_table_json("checkpoint_blobs", limit=100, decode_blob=False))

参数说明：
    table       : 表名，可选 checkpoints、checkpoint_blobs、checkpoint_writes、checkpoint_migrations
    thread_id   : 按线程过滤（checkpoint_migrations 无该列，会自动忽略）
    limit       : 返回行数上限，默认 50
    decode_blob : 是否将 msgpack blob 反序列化为可读内容，默认 True

仅用于本地调试查看数据，不集成进 Agent 工具链。
"""
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row

# ============================================================================
# [!] 常量补全（历史遗留问题）：以下常量在原文件中被引用却从未定义，
#     直接调用 inspect_table、inspect_checkpoint_detail 会抛 NameError。
# ============================================================================

# 可查询的四张 checkpoint 表
TABLES = ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations')

# 含 thread_id 列、可按线程过滤的表（checkpoint_migrations 无该列）
THREAD_FILTERED_TABLES = {'checkpoints', 'checkpoint_blobs', 'checkpoint_writes'}

# 各表默认排序列（按最新数据降序展示）
ORDER_BY_COLUMNS = {'checkpoints': 'checkpoint_id', 'checkpoint_blobs': 'version', 'checkpoint_writes': 'checkpoint_id', 'checkpoint_migrations': 'v'}

# msgpack blob 列名（需按 type 反序列化）
BLOB_VALUE_COLUMN = 'blob'

# bytea[] 数组列（元素形如 [channel, type, blob]，需逐元素反序列化）
DETAIL_BLOB_ARRAY_COLUMNS = {'channel_values', 'pending_writes', 'sends'}


SELECT_CHECKPOINT_WITH_BLOBS = """
select
    thread_id,
    checkpoint,
    checkpoint_ns,
    checkpoint_id,
    parent_checkpoint_id,
    metadata,
    (
        select array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])
        from jsonb_each_text(checkpoint -> 'channel_versions')
        inner join checkpoint_blobs bl
            on bl.thread_id = checkpoints.thread_id
            and bl.checkpoint_ns = checkpoints.checkpoint_ns
            and bl.channel = jsonb_each_text.key
            and bl.version = jsonb_each_text.value
    ) as channel_values,
    (
        select
        array_agg(array[cw.task_id::text::bytea, cw.channel::bytea, cw.type::bytea, cw.blob] order by cw.task_id, cw.idx)
        from checkpoint_writes cw
        where cw.thread_id = checkpoints.thread_id
            and cw.checkpoint_ns = checkpoints.checkpoint_ns
            and cw.checkpoint_id = checkpoints.checkpoint_id
    ) as pending_writes
from checkpoints
where thread_id = %s and checkpoint_ns = %s
order by checkpoint_id desc
limit 1
"""

# [!] 已移除历史查询语句 SELECT_PENDING_SENDS_SQL：其引用未定义的 TASKS 常量，且全项目无调用点。

# [!] 以下为从 LangGraph 源码拷贝的历史遗留写库语句（UPSERT、INSERT），本只读调试脚本无调用点，可酌情删除。
UPSERT_CHECKPOINT_BLOBS_SQL = """
    INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, channel, version) DO NOTHING
"""

UPSERT_CHECKPOINTS_SQL = """
    INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
    DO UPDATE SET
        checkpoint = EXCLUDED.checkpoint,
        metadata = EXCLUDED.metadata;
"""

UPSERT_CHECKPOINT_WRITES_SQL = """
    INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO UPDATE SET
        channel = EXCLUDED.channel,
        type = EXCLUDED.type,
        blob = EXCLUDED.blob;
"""

INSERT_CHECKPOINT_WRITES_SQL = """
    INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) DO NOTHING
"""


def _get_base_dir() -> Path:
    """获取项目根目录：向上递归查找包含 pyproject.toml 的目录（参考 main.py）。"""
    current_path = Path(__file__).resolve()
    for parent in [current_path] + list(current_path.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


BASE_DIR = _get_base_dir()

# 优先读取系统环境变量中指定的 APP_ENV（如 production、development）
app_env = os.getenv("APP_ENV", "development")


def _resolve_env_file() -> Path | None:
    """按候选顺序解析 .env：当前工作目录优先，其次项目根目录；优先带 APP_ENV 后缀。"""
    candidates = [
        Path.cwd() / ".env.{0}".format(app_env),
        Path.cwd() / ".env",
        BASE_DIR / ".env.{0}".format(app_env),
        BASE_DIR / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_env() -> Path | None:
    """加载环境配置（复用 database_url），返回命中的环境文件路径或 None。"""
    env_file = _resolve_env_file()
    if env_file is not None:
        load_dotenv(env_file)
    return env_file


def _decode_value(value):
    """将不可 JSON 序列化的字段（bytea 等）转换为可序列化形式，UTF-8 优先、hex 兜底。"""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            return {"encoding": "utf-8", "data": raw.decode("utf-8")}
        except UnicodeDecodeError:
            return {"encoding": "hex", "data": raw.hex()}
    if isinstance(value, (int, float, str, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)



def _to_readable(obj, depth=0):
    """将反序列化得到的对象递归转换为可 JSON 序列化的可读结构。"""
    if depth > 6:
        return str(obj)
    if obj is None or isinstance(obj, (int, float, bool, str)):
        return obj
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return _decode_value(obj)
    if isinstance(obj, dict):
        return {str(k): _to_readable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_readable(item, depth + 1) for item in obj]
    if isinstance(obj, BaseMessage):
        return {
            "message_type": obj.type,
            "id": obj.id,
            "content": _to_readable(obj.content, depth + 1),
            "tool_calls": _to_readable(getattr(obj, "tool_calls", None), depth + 1),
            "additional_kwargs": _to_readable(getattr(obj, "additional_kwargs", {}) or {}, depth + 1),
        }
    if hasattr(obj, "model_dump"):
        try:
            return _to_readable(obj.model_dump(), depth + 1)
        except Exception:
            pass
    return str(obj)


def _decode_blob(blob_type, blob_bytes, serde):
    """用 JsonPlusSerializer 反序列化 msgpack blob；失败则回退 UTF-8 或 hex 原始内容。"""
    if blob_bytes is None:
        return None
    raw = bytes(blob_bytes)
    if not raw:
        return {"encoding": "utf-8", "data": ""}
    try:
        obj = serde.loads_typed((blob_type, raw))
        return {"encoding": "deserialized", "type": blob_type, "data": _to_readable(obj)}
    except Exception as exc:
        fallback = _decode_value(raw)
        fallback["deserialize_error"] = "{0}: {1}".format(type(exc).__name__, exc)
        return fallback


def _row_to_dict(row, serde=None, decode_blob=True):
    """逐字段解码；blob 列在开启解码时尝试反序列化。"""
    # [!] 潜在脆弱点：blob_type 依赖行内的 type 列；checkpoint_blobs 与 checkpoint_writes
    #     的 SELECT * 恰好含该列，但若查询改为仅取 blob 列，将拿不到 type 导致解码失败。
    blob_type = row.get("type")
    result = {}
    for key, value in row.items():
        if key == BLOB_VALUE_COLUMN and decode_blob and serde is not None:
            result[key] = _decode_blob(blob_type, value, serde)
        else:
            result[key] = _decode_value(value)
    return result


def query_table(conn, table, thread_id=None, limit=50, serde=None, decode_blob=True):
    """按表名查询（命中 ORDER_BY_COLUMNS 的表按指定列降序），返回可序列化的行列表。"""
    where = ""
    params = []
    if thread_id and table in THREAD_FILTERED_TABLES:
        where = " WHERE thread_id = %s"
        params.append(thread_id)
    order_col = ORDER_BY_COLUMNS.get(table)
    order = " ORDER BY {0} DESC".format(order_col) if order_col else ""
    # [!] table 与 order_col 直接拼接进 SQL：当前表名经 TABLES 白名单校验（见 inspect_table），
    #     若调用方绕过校验直接调用本函数，存在 SQL 注入风险。
    sql = "SELECT * FROM {0}{1}{2} LIMIT %s".format(table, where, order)
    params.append(limit)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_dict(r, serde, decode_blob) for r in rows]



def inspect_table(
    table: str,
    thread_id: str | None = None,
    limit: int = 50,
    decode_blob: bool = True,
    database_url: str | None = None,
) -> dict:
    """查询指定表的持久化数据，返回可 JSON 序列化的 dict（核心入口，参数由调用方传入）。

    Args:
        table: 目标表名，必须是 TABLES 中的一员。
        thread_id: 可选线程过滤条件（仅对含 thread_id 列的表生效）。
        limit: 返回行数上限，默认 50。
        decode_blob: 是否将 msgpack blob 反序列化为可读结构，默认 True。
        database_url: 可显式传入连接串，缺省时读取环境变量 database_url。

    Returns:
        dict: 含 table、thread_id、row_count、rows 字段，失败时含 error 字段。
    """
    result = {
        "table": table,
        "thread_id": thread_id,
        "row_count": 0,
        "rows": [],
    }

    if table not in TABLES:
        result["error"] = "不支持的 table: {0}（可选: {1}）".format(table, "、".join(TABLES))
        return result

    env_file = _load_env()
    if env_file is not None:
        print("[Config] loaded env file: " + str(env_file), file=sys.stderr)
    else:
        print("[Config] env file not found, using system environment.", file=sys.stderr)

    url = database_url or os.getenv("database_url")
    if not url:
        result["error"] = "环境变量 database_url 未设置"
        return result

    serde = JsonPlusSerializer()
    try:
        with psycopg.connect(url, row_factory=dict_row) as conn:
            rows = query_table(conn, table, thread_id, limit, serde=serde, decode_blob=decode_blob)
        result["rows"] = rows
        result["row_count"] = len(rows)
    except Exception as exc:
        result["error"] = "{0}: {1}".format(type(exc).__name__, exc)
    return result


def inspect_table_json(
    table: str,
    thread_id: str | None = None,
    limit: int = 50,
    decode_blob: bool = True,
    indent: int | None = 2,
    database_url: str | None = None,
) -> str:
    """按参数查询并返回 JSON 字符串（inspect_table 的便捷封装）。"""
    payload = inspect_table(table, thread_id, limit, decode_blob, database_url)
    return json.dumps(payload, ensure_ascii=False, indent=indent)




def _decode_blob_array(items, serde):
    """将 [..., type, blob] 形式的 bytea 数组按 type 反序列化末位 blob。"""
    decoded = [_to_readable(item) for item in items]
    if len(items) >= 2 and isinstance(items[-2], (bytes, bytearray, memoryview)) and isinstance(items[-1], (bytes, bytearray, memoryview)):
        blob_type = bytes(items[-2]).decode("utf-8", "replace")
        decoded[-1] = _decode_blob(blob_type, items[-1], serde)
    return decoded


def _row_to_detail(row, serde):
    """专用行解码：blob 数组列按 type 反序列化，其余字段走可读化转换。"""
    result = {}
    for key, value in row.items():
        if key in DETAIL_BLOB_ARRAY_COLUMNS and isinstance(value, (list, tuple)):
            result[key] = [_decode_blob_array(item, serde) for item in value]
        else:
            result[key] = _to_readable(value)
    return result


def inspect_checkpoint_detail(
    thread_id: str,
    checkpoint_id: str,
    checkpoint_ns: str = "",
    database_url: str | None = None,
) -> dict:
    """执行 LangGraph 官方 get_tuple 查询：取回指定 checkpoint 及其 channel_values 与 pending_writes。

    Args:
        thread_id: 目标线程 ID。
        checkpoint_id: 目标检查点 ID。
        checkpoint_ns: 检查点命名空间，默认空字符串。
        database_url: 可显式传入连接串，缺省时读取环境变量 database_url。

    Returns:
        dict: 含 table、thread_id、checkpoint_id、row_count、rows 字段；失败时含 error 字段。
    """
    result = {
        "table": "checkpoints + channel_values + pending_writes",
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "row_count": 0,
        "rows": [],
    }

    env_file = _load_env()
    if env_file is not None:
        print("[Config] loaded env file: " + str(env_file), file=sys.stderr)

    url = database_url or os.getenv("database_url")
    if not url:
        result["error"] = "环境变量 database_url 未设置"
        return result

    serde = JsonPlusSerializer()
    try:
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # [!] 功能缺陷：入参 checkpoint_id 未参与 SQL 过滤，SELECT_CHECKPOINT_WITH_BLOBS
                #     仅按 thread_id 与 checkpoint_ns 查询并返回该线程全部 checkpoint（降序），
                #     checkpoint_id 形同虚设；如需精确定位，应在 SQL 追加 and checkpoint_id = %s。
                cur.execute(SELECT_CHECKPOINT_WITH_BLOBS, (thread_id, checkpoint_ns))
                rows = [_row_to_detail(r, serde) for r in cur.fetchall()]
        result["rows"] = rows
        result["row_count"] = len(rows)
    except Exception as exc:
        result["error"] = "{0}: {1}".format(type(exc).__name__, exc)
    return result


if __name__ == "__main__":
    # 直接运行本脚本时，按需修改以下参数即可查看目标数据
    TARGET_TABLE = "checkpoint_writes"
    TARGET_THREAD_ID = "123027"
    TARGET_LIMIT = 10
    DECODE_BLOB = True

    # print(inspect_table_json(
    #     TARGET_TABLE,
    #     thread_id=TARGET_THREAD_ID,
    #     limit=TARGET_LIMIT,
    #     decode_blob=DECODE_BLOB,
    # ))

    print(inspect_checkpoint_detail(
        thread_id=TARGET_THREAD_ID,
        checkpoint_id="",
    ))