"""统一响应包、错误码与异常分类。

分类依据 agent-craft/error-matrix：可重试 / 需人工 / 终止。
本模块只负责「给前端一个稳定契约」，重试由调用方按 code 判断。
"""

import uuid
from datetime import datetime, timezone

ERROR_STATUS = {
    "INVALID_INPUT": 400,
    "NOT_FOUND": 404,
    "OUT_OF_SCOPE": 422,
    "INSUFFICIENT_DATA": 422,
    "OUT_OF_BOUNDS": 422,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
    "MODEL_UNAVAILABLE": 503,
    "KNOWLEDGE_UNAVAILABLE": 503,
}

# 是否值得客户端重试：只对临时性失败为真，参数类错误重试没有意义。
RETRYABLE = {"RATE_LIMITED", "MODEL_UNAVAILABLE", "KNOWLEDGE_UNAVAILABLE", "INTERNAL"}


class ApiError(Exception):
    def __init__(self, code: str, message: str, detail=None):
        if code not in ERROR_STATUS:
            raise ValueError("未知错误码：%s" % code)
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    @property
    def http_status(self) -> int:
        return ERROR_STATUS[self.code]

    def as_body(self, trace_id: str) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": self.detail,
                "retryable": self.code in RETRYABLE,
            },
            "trace_id": trace_id,
        }


def new_trace_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return "tr_%s_%s" % (stamp, uuid.uuid4().hex[:6])


def ok_body(data, trace_id: str) -> dict:
    return {"ok": True, "data": data, "trace_id": trace_id}
