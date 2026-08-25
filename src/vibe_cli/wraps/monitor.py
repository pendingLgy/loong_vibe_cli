import functools
import time

from langgraph.errors import GraphInterrupt

from vibe_cli.env.logger_config import logger


def monitor_node(node_name: str):
    """LangGraph 节点监控装饰器"""

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(state, *args, **kwargs):
            start_time = time.time()
            logger.info(f"🟢 [Node Start] 节点 [{node_name}] 开始执行...")
            try:
                # 执行原节点逻辑
                result = await func(state, *args, **kwargs)
                cost_time = time.time() - start_time
                logger.info(f"✅ [Node Success] 节点 [{node_name}] 执行成功 | 耗时: {cost_time:.2f}s")
                return result
            except GraphInterrupt as e:
                cost_time = time.time() - start_time
                logger.info(f"❌ [Node Error] 节点 [{node_name}] 执行崩溃 | 耗时: {cost_time:.2f}s | 错误: {str(e)}")
                raise e
            except Exception as e:
                cost_time = time.time() - start_time
                logger.exception(
                    f"❌ [Node Error] 节点 [{node_name}] 执行崩溃 | 耗时: {cost_time:.2f}s | 错误: {str(e)}")
                raise e

        @functools.wraps(func)
        def sync_wrapper(state, *args, **kwargs):
            start_time = time.time()
            logger.info(f"🟢 [Node Start] 节点 [{node_name}] 开始执行...")
            try:
                result = func(state, *args, **kwargs)
                cost_time = time.time() - start_time
                logger.info(f"✅ [Node Success] 节点 [{node_name}] 执行成功 | 耗时: {cost_time:.2f}s")
                return result
            except GraphInterrupt as e:
                cost_time = time.time() - start_time
                logger.info(f"❌ [Node Error] 节点 [{node_name}] 执行崩溃 | 耗时: {cost_time:.2f}s | 错误: {str(e)}")
                raise e
            except Exception as e:
                cost_time = time.time() - start_time
                logger.exception(
                    f"❌ [Node Error] 节点 [{node_name}] 执行崩溃 | 耗时: {cost_time:.2f}s | 错误: {str(e)}")
                raise e

        # 自动识别并适配异步或同步节点
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
