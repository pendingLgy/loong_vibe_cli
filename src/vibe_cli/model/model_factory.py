import os

from langchain_openai import ChatOpenAI


# 1. 动态模型工厂方法
def get_model(provider: str):
    """根据厂商名称动态实例化对应的 Model"""

    try:
        temperature = float(os.getenv("temperature", "0.0") or "0.0")
    except (ValueError, TypeError):
        temperature = 0.0

    if provider == "openai":
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            temperature=temperature
        )
    elif provider == "deepseek":
        return ChatOpenAI(
            model=os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-chat"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=temperature
        )
    # elif provider == "claude":
    # from langchain_anthropic import ChatAnthropic
    # return ChatAnthropic(
    #     model=os.getenv("CLAUDE_MODEL_NAME", "claude-3-5-sonnet-20240620"),
    #     api_key=os.getenv("ANTHROPIC_API_KEY"),
    #     temperature=0.0
    # )
    else:
        raise ValueError(f"未知的模型厂商: {provider}")


# 2. 在不同的 Node 中使用不同的模型
def code_planner_node(state):
    """节点 A：使用 Claude 进行架构设计和复杂推理"""
    model = get_model("claude")
    response = model.invoke(state["messages"])
    return {"messages": [response]}


def fast_checker_node(state):
    """节点 B：使用 DeepSeek 进行快速安全检查或格式化"""
    model = get_model("deepseek")
    response = model.invoke(state["messages"])
    return {"messages": [response]}
