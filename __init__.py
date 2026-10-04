# -*- coding: utf-8 -*-
"""
BSAI Hardware Performance Monitor —— ComfyUI 插件入口
======================================================
- 注册 `BSAIHardwarePerfMonitor` 节点（nodes.py）
- 注册 `GET /bsai/perf` 数据端点，供前端仪表盘轮询
- 声明 `web/` 目录，前端 HUD 自动加载

路由注册采用与 BSAI-Contextual-Series 相同的 PromptServer 模式，
并做容错：极端情况下 instance 不可用时插件仍可加载（仅缺路由）。
"""

import os
import json
import asyncio

import server
from aiohttp import web

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .perf_collector import collect_all, decide_hardware

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


async def bsai_perf(request):
    """多硬件协同实时性能数据（前端仪表盘轮询）。

    关键：collect_all() 内含同步 HTTP 轮询（XPU worker / NPU 健康检查）。
    绝不能直接在事件循环主线程里同步执行，否则会阻塞整个 ComfyUI server
    （前端卡死在启动 logo、API 全部超时）。
    这里用 asyncio.to_thread 把同步采集放到工作线程，事件循环保持响应。
    """
    try:
        data = await asyncio.to_thread(collect_all)
        return web.json_response(data)
    except Exception as e:
        return web.json_response({"ts": 0, "error": str(e)})


async def bsai_hw_decide(request):
    """智能硬件路由决策 API：根据实时负载推荐最佳执行设备。

    查询参数：
        task: face_detect | face_restore | vae_decode | sampling | general
        prefer: performance | balanced | offload

    其他插件可直接调用此接口来决定任务应该分配到哪个硬件，
    实现真正的"多硬件协同"——不只是监控，还要主动调度。
    """
    try:
        task_type = request.query.get("task", "face_detect")
        prefer = request.query.get("prefer", "balanced")
        result = await asyncio.to_thread(
            lambda: decide_hardware(task_type=task_type, prefer=prefer)
        )
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"recommended": "GPU1", "error": str(e)})


try:
    _ps = server.PromptServer.instance
    if _ps is not None and hasattr(_ps, "routes"):
        _ps.routes.get("/bsai/perf")(bsai_perf)
        _ps.routes.get("/bsai/hw/decide")(bsai_hw_decide)
        print("[BSAI-Perf] GET /bsai/perf + /bsai/hw/decide 已注册（多硬件协同性能监视 + 智能路由决策，异步采集）")
    else:
        print("[BSAI-Perf] PromptServer.instance 未就绪，/bsai/perf 路由稍后由节点触发注册")
except Exception as e:
    print("[BSAI-Perf] 路由注册容错跳过:", e)
