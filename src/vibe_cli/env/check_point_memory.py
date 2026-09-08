import os
from typing import Optional

from langgraph.checkpoint.postgres import PostgresSaver  # 同步 Checkpointer
from loguru import logger
from psycopg_pool import ConnectionPool  # 同步连接池

_checkpointer_instance: Optional[PostgresSaver] = None
_pool_instance: Optional[ConnectionPool] = None


def postgres_memory() -> PostgresSaver:
    """初始化并返回 LangGraph PostgreSQL 同步持久化 Checkpointer 实例（单例模式）。"""
    global _checkpointer_instance, _pool_instance

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    database_url = os.getenv("database_url", "")
    if not database_url:
        raise ValueError("环境变量 'database_url' 未设置！")

    conn_url = database_url

    # 1. 创建同步连接池（创建时即自动 open）
    _pool_instance = ConnectionPool(
        name="postgres_memory",
        conninfo=conn_url,
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )

    # 2. 创建同步 Checkpointer 并建表
    checkpointer = PostgresSaver(_pool_instance)
    checkpointer.setup()  # 同步建表

    _checkpointer_instance = checkpointer
    logger.success("PostgreSQL 同步 Checkpointer 初始化成功")

    return _checkpointer_instance


def close_postgres_memory():
    """同步关闭连接池"""
    global _pool_instance, _checkpointer_instance
    if _pool_instance is not None:
        _pool_instance.close()
        _pool_instance = None
        _checkpointer_instance = None
        logger.info("PostgreSQL 同步连接池已关闭")
