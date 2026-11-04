"""问答任务管理：事实卡同步返回，候选异步生成，前端轮询。

为什么要两段式：模型一次 2–4 秒，同步等待会让页面卡住；
事实卡本身 <50ms 就能给出，先让人看到可核验的部分。
"""

import threading
import time
import uuid

from . import sidecar
from .errors import ApiError

JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
MAX_JOBS = 50


def start(store, work_id, question: str, model: str, settings=None) -> dict:
    with _LOCK:
        # 只保留最近的任务，避免长时间运行内存无限增长
        while len(JOBS) >= MAX_JOBS:
            oldest = min(JOBS.values(), key=lambda job: job["created_at"])
            JOBS.pop(oldest["id"], None)
        job_id = "qa_%s" % uuid.uuid4().hex[:10]
        JOBS[job_id] = {
            "id": job_id,
            "state": "running",
            "created_at": time.time(),
            "result": None,
            "error": None,
            "cancelled": False,
        }

    thread = threading.Thread(
        target=_run, args=(job_id, store, work_id, question, model), daemon=True
    )
    thread.start()
    return {"qa_id": job_id, "state": "running"}


def _run(job_id: str, store, work_id, question: str, model: str, settings=None) -> None:
    try:
        result = sidecar.generate(store, work_id, question=question, model=model, settings=settings)
        with _LOCK:
            job = JOBS.get(job_id)
            if job and not job["cancelled"]:
                job.update({"state": "done", "result": result})
    except ApiError as exc:
        with _LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({"state": "failed", "error": {"code": exc.code, "message": exc.message}})
    except Exception as exc:  # 兜底：任务失败必须落地，不能静默
        with _LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({"state": "failed", "error": {"code": "INTERNAL", "message": repr(exc)}})


def get(job_id: str) -> dict:
    with _LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ApiError("NOT_FOUND", "问答任务不存在", {"qa_id": job_id})
        snapshot = dict(job)
    if snapshot["state"] == "running":
        return {"qa_id": job_id, "state": "running", "result": None, "error": None}
    return {
        "qa_id": job_id,
        "state": snapshot["state"],
        "result": snapshot["result"],
        "error": snapshot["error"],
    }


def cancel(job_id: str) -> dict:
    with _LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ApiError("NOT_FOUND", "问答任务不存在", {"qa_id": job_id})
        job["cancelled"] = True
        if job["state"] == "running":
            job["state"] = "cancelled"
    return {"qa_id": job_id, "state": "cancelled"}
