"""可注入时钟。

业务时间一律通过 ``clock.now()`` 获取，测试和逾期升级任务可以用
``freeze_time`` 上下文管理器注入任意当前时间，保证逾期判定可复现。
"""
import contextlib
import threading

from django.utils import timezone

_state = threading.local()


def now():
    """返回当前业务时间（感知时区）。"""
    frozen = getattr(_state, "value", None)
    if frozen is not None:
        return frozen
    return timezone.now()


@contextlib.contextmanager
def freeze_time(dt):
    """在上下文内把 clock.now() 固定为 dt，支持嵌套。"""
    previous = getattr(_state, "value", None)
    _state.value = dt
    try:
        yield dt
    finally:
        _state.value = previous
