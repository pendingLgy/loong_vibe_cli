import os

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from vibe_cli.env.logger_config import logger


async def postgres_memory() -> AsyncPostgresSaver:
    '''初始化并返回 LangGraph PostgreSQL 同步持久化 Checkpointer 实例。
       直接返回对象，不使用任何上下文管理器或 with 语句。
    '''

    DATABASE_URL = os.getenv('database_url')
    conn_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    # 使用异步连接池
    pool = AsyncConnectionPool(
        name='postgres_memory',
        conninfo=conn_url,
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False
    )
    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # 异步建表

    logger.success("postgresql init success")
    return checkpointer
