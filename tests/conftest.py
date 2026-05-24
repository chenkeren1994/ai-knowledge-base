"""Pytest 全局配置 — 注册自定义标记，避免 PytestUnknownMarkWarning。"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: LLM 调用测试，速度较慢，可用 '-m \"not slow\"' 跳过",
    )
